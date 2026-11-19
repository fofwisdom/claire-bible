/* Claire Bible — feed view of the federation client of /syndication (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, unit FP4-F3).
 *
 * Rules of this file:
 * - Feeds are first collection of the account. They work in both server modes, also when the account is not a federation actor.
 * - The feed view uses the list pane and the reader pane of the client view. While a feed scope is active (Feeds.active), syndication_client.js does not render the timeline into these panes.
 * - Feed HTML is not trusted. The item list has plain text only. The body of an item is a sandbox iframe from the render URL that the server gives (the usercontent origin). This file never puts feed HTML into the page.
 * - Each text of this view is a catalog key (ClaireI18n). Each value from the server goes through esc().
 * - This file loads before syndication_client.js. That file calls Feeds.init() with its functions.
 */
(function () {
  "use strict";

  const ic = (name) => (window.ClaireIcons ? window.ClaireIcons.svg(name) : "");
  const bridge = window.ClaireSyndication;
  const cfg = window.__CLAIRE_CONFIG || {};
  const I18n = window.ClaireI18n;
  if (!bridge || !I18n || !document.getElementById("client-feeds")) return;
  const tr = (key, params) => I18n.t(key, params);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const $ = (id) => document.getElementById(id);

  const IS_OWNER = cfg.authScope === "owner";
  const PAGE = 50;

  const F = {
    host: null,
    feeds: null,
    // The scope of the list: "all", "starred", "folder:<name>", or "feed:<id>".
    scope: "all",
    unreadOnly: false,
    items: [],
    nextBeforeId: null,
    loaded: false,
    loading: false,
    error: "",
    selectedId: null,
    detail: null,
    candidates: null,
    ingestItemId: null,
    seq: 0
  };

  // The client view opens with the timeline in both server modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5). A feed selection makes the feed view active.
  const Feeds = { active: false };

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

  function timeText(seconds) {
    if (!seconds) return "";
    const date = new Date(seconds * 1000);
    return Date.now() - date.getTime() < 86400000 ? date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : date.toLocaleDateString();
  }

  function setMessage(text) {
    const el = $("client-feed-msg");
    if (el) el.textContent = text || "";
  }

  // --- Tree: folders and feeds -----------------------------------------------

  function feedList() {
    return F.feeds || [];
  }

  function unreadTotal() {
    return feedList().reduce((n, feed) => n + (Number(feed.unread) || 0), 0);
  }

  function scopeTitle() {
    if (F.scope === "all") return tr("synd.feeds.scope.all");
    if (F.scope === "starred") return tr("synd.feeds.scope.starred");
    if (F.scope.startsWith("folder:")) return F.scope.slice(7);
    const feed = feedList().find((f) => `feed:${f.id}` === F.scope);
    return feed ? feed.title || feed.url : tr("synd.feeds.title");
  }

  function scopeQuery() {
    if (F.scope === "starred") return "&starred=1";
    if (F.scope.startsWith("folder:")) return `&folder=${encodeURIComponent(F.scope.slice(7))}`;
    if (F.scope.startsWith("feed:")) return `&feed_id=${encodeURIComponent(F.scope.slice(5))}`;
    return "";
  }

  // The feeds that the scope contains ("mark all as read" uses them).
  function scopeFeeds() {
    if (F.scope.startsWith("feed:")) return feedList().filter((f) => `feed:${f.id}` === F.scope);
    if (F.scope.startsWith("folder:")) return feedList().filter((f) => f.folder === F.scope.slice(7));
    return F.scope === "all" ? feedList() : [];
  }

  function treeButton(scope, icon, label, count, title) {
    const on = Feeds.active && F.scope === scope;
    return `<button type="button" class="tt-tree-item ${on ? "active" : ""}" data-feed-scope="${esc(scope)}" ${title ? `title="${esc(title)}"` : ""} ${on ? 'aria-current="true"' : ""}><span class="synd-feed-name">${icon} ${esc(label)}</span><span class="tt-count-pill ${count > 0 ? "fresh" : ""}">${count ? esc(count) : ""}</span></button>`;
  }

  function feedRow(feed) {
    const failed = Number(feed.failure_count) > 0;
    const title = failed ? tr("synd.feeds.poll_failed", { error: feed.last_error || feed.last_status || "" }) : feed.url;
    return `<div class="synd-feed-row">${treeButton(`feed:${feed.id}`, failed ? ic("warning") : ic("broadcast"), feed.title || feed.url, Number(feed.unread) || 0, title)}<button type="button" class="tt-icon-btn" data-feed-remove="${esc(feed.id)}" title="${esc(tr("synd.feeds.unsubscribe"))}" aria-label="${esc(tr("synd.feeds.unsubscribe_name", { name: feed.title || feed.url }))}">✕</button></div>`;
  }

  function renderTree() {
    const el = $("client-feeds");
    if (!el) return;
    const pill = $("client-feeds-unread");
    const total = unreadTotal();
    if (pill) { pill.textContent = total ? String(total) : ""; pill.classList.toggle("fresh", total > 0); }
    const folders = $("client-feed-folders");
    if (F.feeds === null) { el.innerHTML = ""; return; }
    const names = [...new Set(feedList().map((f) => f.folder).filter(Boolean))].sort();
    if (folders) folders.innerHTML = names.map((name) => `<option value="${esc(name)}"></option>`).join("");
    let html = treeButton("all", ic("inbox"), tr("synd.feeds.scope.all"), total) + treeButton("starred", ic("star"), tr("synd.feeds.scope.starred"), 0);
    names.forEach((name) => {
      const inFolder = feedList().filter((f) => f.folder === name);
      const unread = inFolder.reduce((n, f) => n + (Number(f.unread) || 0), 0);
      html += `<div class="synd-feed-folder">${treeButton(`folder:${name}`, ic("folder"), name, unread)}<div class="synd-feed-children">${inFolder.map(feedRow).join("")}</div></div>`;
    });
    html += feedList().filter((f) => !f.folder).map(feedRow).join("");
    if (!feedList().length) html += `<div class="synd-note">${esc(tr("synd.feeds.empty_tree"))}</div>`;
    el.innerHTML = html;
  }

  function renderCandidates() {
    const el = $("client-feed-candidates");
    if (!el) return;
    if (!F.candidates) { el.innerHTML = ""; return; }
    el.innerHTML = F.candidates.map((feed, index) => `<div class="synd-feed-row"><span class="synd-feed-name" title="${esc(feed.url)}">${ic("broadcast")} ${esc(feed.title || feed.url)}</span>${feed.subscribed
      ? `<span class="tt-count-pill">${esc(tr("synd.feeds.subscribed"))}</span>`
      : `<button type="button" class="tt-btn synd-mini" data-feed-candidate="${index}">${esc(tr("synd.feeds.subscribe_button"))}</button>`}</div>`).join("");
  }

  async function loadFeeds() {
    if (!IS_OWNER) return;
    const result = await api("GET", "/api/feeds");
    if (!result.ok) { F.feeds = F.feeds || []; setMessage(tr("synd.error.request", { error: errorText(result) })); return; }
    F.feeds = result.data.feeds || [];
    renderTree();
    if (F.host) F.host.renderStageBar();
  }

  // --- Item list ---------------------------------------------------------------

  async function loadItems(reset) {
    if (!IS_OWNER) return;
    const seq = ++F.seq;
    F.loading = true;
    if (reset) { F.items = []; F.nextBeforeId = null; }
    render();
    const cursor = !reset && F.nextBeforeId ? `&before_id=${encodeURIComponent(F.nextBeforeId)}` : "";
    const result = await api("GET", `/api/feeds/items?limit=${PAGE}${scopeQuery()}${F.unreadOnly ? "&unread=1" : ""}${cursor}`);
    if (seq !== F.seq) return;
    F.loading = false;
    F.loaded = true;
    if (!result.ok) {
      F.error = errorText(result);
    } else {
      F.error = "";
      F.items = reset ? result.data.items : F.items.concat(result.data.items);
      F.nextBeforeId = result.data.next_before_id;
    }
    render();
  }

  function itemBadges(item) {
    const badges = [];
    if (item.linked) badges.push(`<span class="tt-link-dot linked">${ic("book")} ${esc(tr("synd.client.badge.ingested"))}</span>`);
    if (item.timeline_item_id) badges.push(`<span class="tt-link-dot unlinked">${ic("merge")} ${esc(tr("synd.feeds.badge.in_timeline"))}</span>`);
    return badges.join("");
  }

  function starButton(item, cls) {
    const label = tr(item.starred ? "synd.client.action.unstar" : "synd.client.action.star");
    return `<button type="button" class="${cls} ${item.starred ? "starred" : ""}" data-feed-star="${esc(item.id)}" title="${esc(label)}" aria-label="${esc(label)}" aria-pressed="${item.starred ? "true" : "false"}">${item.starred ? "★" : "☆"}${cls === "tt-btn" ? ` ${esc(label)}` : ""}</button>`;
  }

  function documentButtons(item, cls, primary) {
    if (item.linked) return `<button type="button" class="${cls}" data-feed-doc="${esc(item.id)}">${ic("book")} ${esc(tr("synd.client.action.open_document"))}</button>`;
    if (!item.url) return "";
    return `<button type="button" class="${cls} ${primary ? "tt-btn-pri" : ""}" data-feed-ingest="${esc(item.id)}">${ic("book")} ${esc(tr("synd.client.action.ingest"))}</button>`;
  }

  function renderList() {
    const el = $("client-list");
    const notice = $("client-notice");
    if (!el || !notice) return;
    notice.hidden = !F.error;
    notice.textContent = F.error ? tr("synd.error.request", { error: F.error }) : "";
    const canMarkAll = scopeFeeds().some((f) => Number(f.unread) > 0);
    const bar = `<div class="synd-feed-bar"><b class="synd-feed-bar-title">${ic("broadcast")} ${esc(scopeTitle())}</b>
      <button type="button" class="tt-btn synd-mini ${F.unreadOnly ? "tt-btn-pri" : ""}" data-feed-unread-only aria-pressed="${F.unreadOnly ? "true" : "false"}">${esc(tr("synd.feeds.unread_only"))}</button>
      <button type="button" class="tt-btn synd-mini" data-feed-mark-all ${canMarkAll ? "" : "disabled"}>${esc(tr("synd.feeds.mark_all_read"))}</button></div>`;
    if (!F.loaded && F.loading) {
      el.innerHTML = `${bar}<div class="synd-empty">${esc(tr("synd.client.loading"))}</div>`;
      return;
    }
    if (!F.items.length) {
      el.innerHTML = `${bar}<div class="synd-empty">${esc(tr(feedList().length ? "synd.feeds.empty_items" : "synd.feeds.empty_tree"))}</div>`;
      return;
    }
    const rows = F.items.map((item) => `<article class="synd-post ${item.unread ? "is-unread" : ""} ${String(F.selectedId) === String(item.id) ? "is-selected" : ""}" data-feed-item-id="${esc(item.id)}" tabindex="0">
        <div class="synd-post-meta"><span class="synd-post-author">${esc(item.feed_title || "")}</span><span class="synd-post-handle">${esc(item.author || "")}</span><span class="synd-post-time">${esc(timeText(item.published_at || item.fetched_at))}</span>${itemBadges(item)}</div>
        <div class="synd-post-title">${esc(item.title || tr("synd.feeds.untitled"))}</div>
        ${item.text ? `<div class="synd-post-text">${esc(item.text)}</div>` : ""}
        <div class="synd-post-actions">${starButton(item, "tt-icon-btn")}
          <button type="button" class="tt-btn synd-mini" data-feed-read="${esc(item.id)}">${esc(tr(item.unread ? "synd.client.action.mark_read" : "synd.client.action.mark_unread"))}</button>
          ${documentButtons(item, "tt-btn synd-mini", false)}
        </div>
      </article>`).join("");
    const more = F.nextBeforeId ? `<div class="synd-more"><button type="button" class="tt-btn" id="feed-load-more" ${F.loading ? "disabled" : ""}>${esc(tr("synd.feeds.load_more"))}</button></div>` : "";
    el.innerHTML = bar + rows + more;
  }

  // --- Reader --------------------------------------------------------------------

  function renderReader() {
    const el = $("client-reader");
    if (!el) return;
    const item = F.detail;
    if (!item || String(item.id) !== String(F.selectedId)) {
      el.innerHTML = `<div class="synd-empty">${esc(tr(F.selectedId ? "synd.client.loading" : "synd.feeds.select_prompt"))}</div>`;
      return;
    }
    const actions = `<div class="synd-reader-actions">${starButton(item, "tt-btn")}
      <button type="button" class="tt-btn" data-feed-read="${esc(item.id)}">${esc(tr(item.unread ? "synd.client.action.mark_read" : "synd.client.action.mark_unread"))}</button>
      ${item.url ? `<button type="button" class="tt-btn" data-feed-share="${esc(item.id)}">${ic("link")} ${esc(tr("synd.feeds.share_link"))}</button>` : ""}
      ${documentButtons(item, "tt-btn", true)}
      ${item.linked ? `<button type="button" class="tt-btn tt-btn-pri" data-feed-publish="${esc(item.id)}">${ic("megaphone")} ${esc(tr("synd.client.action.publish"))}</button>` : ""}
      ${item.timeline_item_id ? `<button type="button" class="tt-btn" data-feed-timeline="${esc(item.timeline_item_id)}">${ic("merge")} ${esc(tr("synd.feeds.open_in_timeline"))}</button>` : ""}
    </div>`;
    const tags = (item.categories || []).slice(0, 12).map((tag) => `<span class="tt-link-dot unlinked">${ic("tag")} ${esc(tag)}</span>`).join("");
    // The frame has no script permission and an opaque origin. The server sends the same policy in the Content-Security-Policy header of the page.
    const body = item.has_body
      ? `<iframe class="synd-frame synd-frame-feed" sandbox="allow-popups allow-popups-to-escape-sandbox" referrerpolicy="no-referrer" title="${esc(tr("synd.feeds.frame_title", { name: item.title || item.feed_title || "" }))}" src="${esc(item.render_url)}"></iframe>`
      : `<div class="synd-empty">${esc(tr("synd.feeds.no_body"))}</div>`;
    el.innerHTML = `<div class="synd-reader">${actions}<section class="synd-entry is-main" data-feed-entry-id="${esc(item.id)}">
      <header class="synd-entry-head">
        <div><b>${esc(item.feed_title || "")}</b> <span class="synd-post-handle">${esc(item.author || "")}</span></div>
        <div class="synd-entry-tools"><span class="synd-post-time">${esc(timeText(item.published_at || item.fetched_at))}</span>${item.url ? `<a class="tt-ext-link" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer nofollow">${esc(tr("synd.client.action.open_original"))} ↗</a>` : ""}</div>
      </header>
      <h3 class="synd-entry-title">${esc(item.title || tr("synd.feeds.untitled"))}</h3>
      ${tags ? `<div class="synd-feed-tags">${tags}</div>` : ""}
      ${body}
    </section></div>`;
  }

  function render() {
    renderTree();
    if (!Feeds.active) return;
    const title = $("client-stream-title");
    if (title) title.textContent = scopeTitle();
    renderList();
    renderReader();
  }

  function findItem(id) {
    const listed = F.items.find((x) => String(x.id) === String(id));
    if (listed) return listed;
    return F.detail && String(F.detail.id) === String(id) ? F.detail : null;
  }

  function applyItem(id, change) {
    const index = F.items.findIndex((x) => String(x.id) === String(id));
    if (index >= 0) F.items[index] = Object.assign({}, F.items[index], change);
    if (F.detail && String(F.detail.id) === String(id)) F.detail = Object.assign({}, F.detail, change);
  }

  async function selectItem(id) {
    F.selectedId = id;
    F.detail = null;
    render();
    const result = await api("GET", `/api/feeds/items/${encodeURIComponent(id)}`);
    if (String(F.selectedId) !== String(id)) return;
    if (!result.ok) {
      $("client-reader").innerHTML = `<div class="synd-empty">${esc(tr("synd.error.request", { error: errorText(result) }))}</div>`;
      return;
    }
    F.detail = result.data.item;
    renderReader();
    // On a narrow screen the reader is below the list.
    if (window.matchMedia("(max-width: 720px)").matches) $("client-reader-pane").scrollIntoView({ block: "start" });
    if (F.detail.unread) patchItem(id, { unread: false });
  }

  async function patchItem(id, change) {
    const before = findItem(id);
    const result = await api("PATCH", "/api/feeds/items", Object.assign({ item_ids: [Number(id)] }, change));
    if (!result.ok) { bridge.ticker(tr("synd.error.request", { error: errorText(result) })); return; }
    if (before && "unread" in change && before.unread !== change.unread) {
      const feed = feedList().find((f) => String(f.id) === String(before.feed_id));
      if (feed) feed.unread = Math.max(0, (Number(feed.unread) || 0) + (change.unread ? 1 : -1));
    }
    applyItem(id, change);
    render();
    if (F.host) F.host.renderStageBar();
  }

  async function markAllRead() {
    const feeds = scopeFeeds().filter((f) => Number(f.unread) > 0);
    for (const feed of feeds) {
      const result = await api("PATCH", "/api/feeds/items", { feed_id: feed.id, unread: false });
      if (!result.ok) { bridge.ticker(tr("synd.error.request", { error: errorText(result) })); break; }
    }
    await loadFeeds();
    await loadItems(true);
  }

  // Link share. A feed has no interaction target (design Section 1.3 rule 7): the share is a new note with the address of the item, or the address goes to the share sheet of the device or to the clipboard when the compose dialog is not available.
  async function shareLink(id) {
    const item = findItem(id);
    if (!item || !item.url) return;
    // Unit FP4-9: the share is a new note with the link. The owner can change the text before it is sent.
    const compose = window.ClaireSyndicationCompose;
    if (compose && compose.available && compose.open({ mode: "new", text: `${item.title ? `${item.title}\n` : ""}${item.url}` })) return;
    try {
      if (navigator.share && window.matchMedia("(pointer: coarse)").matches) { await navigator.share({ title: item.title || "", url: item.url }); return; }
      await navigator.clipboard.writeText(item.url);
      bridge.ticker(tr("synd.feeds.link_copied"));
    } catch (_) {
      bridge.ticker(tr("synd.feeds.link_copy_failed", { url: item.url }));
    }
  }

  // --- Subscribe, discovery, poll, unsubscribe, OPML ------------------------------

  async function subscribe(url, folder) {
    const body = { url };
    if (folder) body.folder = folder;
    const result = await api("POST", "/api/feeds", body);
    if (!result.ok) {
      const code = result.data && result.data.error;
      setMessage(["invalid_url", "feed_limit"].includes(code) ? tr(`synd.feeds.error.${code}`) : tr("synd.error.request", { error: errorText(result) }));
      return;
    }
    F.candidates = null;
    renderCandidates();
    $("client-feed-url").value = "";
    setMessage(tr(result.data.created ? "synd.feeds.subscribed_polling" : "synd.feeds.already_subscribed"));
    const feed = result.data.feed;
    // The first poll gives the items at once. The poller of the server polls again each hour.
    if (result.data.created) {
      const polled = await api("POST", "/api/feeds/poll", { feed_id: feed.id });
      const outcome = polled.ok && polled.data.outcomes && polled.data.outcomes[0];
      setMessage(outcome && ["updated", "not_modified"].includes(outcome.result) ? tr("synd.feeds.poll_done", { count: outcome.added || 0 }) : tr("synd.feeds.poll_failed", { error: (outcome && (outcome.error || outcome.http_status)) || errorText(polled) }));
    }
    await loadFeeds();
    Feeds.select(`feed:${feed.id}`);
  }

  async function submitSubscribe() {
    let url = $("client-feed-url").value.trim();
    const folder = $("client-feed-folder").value.trim();
    if (!url) return;
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(url)) url = `https://${url}`;
    setMessage(tr("synd.feeds.discovering"));
    F.candidates = null;
    renderCandidates();
    const found = await api("POST", "/api/feeds/discover", { url });
    if (!found.ok) {
      setMessage(found.data && found.data.error === "invalid_url" ? tr("synd.feeds.error.invalid_url") : tr("synd.error.request", { error: errorText(found) }));
      return;
    }
    const feeds = found.data.feeds || [];
    if (!feeds.length) {
      setMessage(found.data.error ? tr("synd.feeds.discover_failed", { error: found.data.error }) : tr("synd.feeds.discover_none"));
      return;
    }
    if (feeds.length === 1) { await subscribe(feeds[0].url, folder); return; }
    F.candidates = feeds;
    setMessage(tr("synd.feeds.discover_many", { count: feeds.length }));
    renderCandidates();
  }

  async function pollNow() {
    setMessage(tr("synd.feeds.polling"));
    const result = await api("POST", "/api/feeds/poll", {});
    if (!result.ok) { setMessage(tr("synd.error.request", { error: errorText(result) })); return; }
    const added = (result.data.outcomes || []).reduce((n, o) => n + (Number(o.added) || 0), 0);
    setMessage(tr("synd.feeds.poll_done", { count: added }));
    await loadFeeds();
    if (Feeds.active) await loadItems(true);
  }

  async function unsubscribe(id) {
    const feed = feedList().find((f) => String(f.id) === String(id));
    if (!feed || !window.confirm(tr("synd.feeds.unsubscribe_confirm", { name: feed.title || feed.url }))) return;
    const result = await api("DELETE", "/api/feeds", { feed_id: feed.id });
    if (!result.ok) { setMessage(tr("synd.error.request", { error: errorText(result) })); return; }
    if (F.scope === `feed:${feed.id}`) F.scope = "all";
    await loadFeeds();
    if (Feeds.active) { F.selectedId = null; F.detail = null; await loadItems(true); }
  }

  async function importOpml(file) {
    if (!file) return;
    if (file.size > 1024 * 1024) { setMessage(tr("synd.feeds.opml_too_large")); return; }
    const result = await api("POST", "/api/feeds/opml", { opml: await file.text() });
    if (!result.ok) { setMessage(tr("synd.feeds.opml_failed", { error: (result.data && result.data.reason) || errorText(result) })); return; }
    const counts = result.data.counts || {};
    setMessage(tr("synd.feeds.opml_done", { added: counts.added || 0, duplicate: counts.duplicate || 0, skipped: counts.skipped || 0, failed: counts.failed || 0 }));
    await loadFeeds();
    if (counts.added) pollNow();
  }

  // --- Ingest of one item (design Section 4.4) -------------------------------------
  // The dialog is the dialog of the timeline items (FP4-5). While F.ingestItemId is set, the submit goes to the feed route.

  function openIngestDialog(id) {
    const item = findItem(id);
    const dialog = $("modal-item-ingest");
    if (!item || !dialog) return;
    F.ingestItemId = id;
    $("item-ingest-msg").textContent = "";
    $("modal-item-ingest-title").textContent = tr("synd.feeds.ingest_title");
    $("item-ingest-note").textContent = tr("synd.feeds.ingest_note");
    $("item-ingest-source").textContent = `${item.feed_title || ""} — ${item.url}`;
    $("btn-item-ingest").disabled = false;
    dialog.showModal();
  }

  async function submitIngest() {
    const id = F.ingestItemId;
    const msg = $("item-ingest-msg");
    const button = $("btn-item-ingest");
    button.disabled = true;
    msg.textContent = tr("synd.ingest.running");
    const result = await api("POST", `/api/feeds/items/${encodeURIComponent(id)}/ingest`, {});
    button.disabled = false;
    if (!result.ok || result.data.error || !result.data.linked) {
      msg.textContent = tr("synd.ingest.failed", { error: errorText(result) });
      return;
    }
    $("modal-item-ingest").close();
    bridge.ticker(tr("synd.ingest.done", { id: result.data.linked.document_id }));
    applyItem(id, { linked: result.data.linked });
    render();
    // The new document is in the personal knowledge base of the account (design Section 5.2 rule 3). Read the list again when stage 2 shows that knowledge base.
    if (String(result.data.linked.kb_id) === String(bridge.state.themeId)) bridge.reload();
  }

  function openDocument(id, thenPublish) {
    const item = findItem(id);
    if (!item || !item.linked || !F.host) return;
    F.host.setStage(thenPublish ? "publish" : "ingest");
    bridge.showDocument(item.linked.kb_id, item.linked.document_id).then(() => {
      if (thenPublish) bridge.openDispatchModal(item.linked.document_id);
    });
  }

  // --- Interface for syndication_client.js -------------------------------------------

  Feeds.init = function (host) {
    F.host = host;
    const streams = $("client-streams-folder");
    if (streams) streams.hidden = false;
    if (IS_OWNER) loadFeeds();
  };

  // The client view opens (stage 1).
  Feeds.open = function () {
    if (!IS_OWNER) return;
    if (F.feeds === null) loadFeeds();
    if (Feeds.active && !F.loaded) loadItems(true); else render();
  };

  Feeds.select = function (scope) {
    Feeds.active = true;
    F.scope = scope;
    F.selectedId = null;
    F.detail = null;
    if (F.host) F.host.renderClient();
    loadItems(true);
  };

  // A stream of the timeline was selected.
  Feeds.deactivate = function () {
    Feeds.active = false;
    renderTree();
  };

  Feeds.unreadTotal = unreadTotal;

  // The feed count in the stage bar. It comes after the counts of the timeline, only when a feed item is unread.
  Feeds.decorateStageBar = function () {
    const el = $("stage-collect-counts");
    if (!el || !IS_OWNER || F.feeds === null) return;
    const text = tr("synd.feeds.stage_unread", { count: unreadTotal() });
    if (unreadTotal() > 0) el.textContent = el.textContent ? `${el.textContent} · ${text}` : text;
  };

  window.ClaireSyndicationFeeds = Feeds;

  // --- Events ------------------------------------------------------------------

  $("panel-client").addEventListener("click", (e) => {
    const pick = (name) => { const el = e.target.closest(`[${name}]`); return el ? el.getAttribute(name) : null; };
    const scope = pick("data-feed-scope");
    if (scope) { Feeds.select(scope); return; }
    const remove = pick("data-feed-remove");
    if (remove) { unsubscribe(remove); return; }
    const candidate = pick("data-feed-candidate");
    if (candidate !== null && F.candidates && F.candidates[Number(candidate)]) { subscribe(F.candidates[Number(candidate)].url, $("client-feed-folder").value.trim()); return; }
    if (e.target.closest("#btn-feed-poll")) { pollNow(); return; }
    if (e.target.closest("#btn-feed-import")) { $("client-feed-opml-file").click(); return; }
    if (!Feeds.active) return;
    const star = pick("data-feed-star");
    if (star) { const item = findItem(star); if (item) patchItem(star, { starred: !item.starred }); return; }
    const read = pick("data-feed-read");
    if (read) { const item = findItem(read); if (item) patchItem(read, { unread: !item.unread }); return; }
    const ingest = pick("data-feed-ingest");
    if (ingest) { openIngestDialog(ingest); return; }
    const doc = pick("data-feed-doc");
    if (doc) { openDocument(doc, false); return; }
    const publish = pick("data-feed-publish");
    if (publish) { openDocument(publish, true); return; }
    const share = pick("data-feed-share");
    if (share) { shareLink(share); return; }
    const timeline = pick("data-feed-timeline");
    if (timeline && F.host) { Feeds.deactivate(); F.host.selectTimelineItem(timeline); return; }
    if (e.target.closest("[data-feed-unread-only]")) { F.unreadOnly = !F.unreadOnly; loadItems(true); return; }
    if (e.target.closest("[data-feed-mark-all]")) { markAllRead(); return; }
    if (e.target.closest("#feed-load-more")) { loadItems(false); return; }
    if (e.target.closest("a, button, input")) return;
    const row = pick("data-feed-item-id");
    if (row) selectItem(row);
  });

  $("client-list").addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    const row = e.target.closest("[data-feed-item-id]");
    if (!row || e.target !== row) return;
    e.preventDefault();
    selectItem(row.getAttribute("data-feed-item-id"));
  });

  $("client-feed-form").addEventListener("submit", (e) => { e.preventDefault(); submitSubscribe(); });
  $("client-feed-opml-file").addEventListener("change", (e) => { const file = e.target.files && e.target.files[0]; e.target.value = ""; importOpml(file); });

  // The ingest dialog of the timeline items has its submit listener in syndication_client.js. This listener runs first (capture on the dialog) and takes the submit only for a feed item.
  const ingestDialog = $("modal-item-ingest");
  if (ingestDialog) {
    ingestDialog.addEventListener("submit", (e) => {
      if (F.ingestItemId === null) return;
      e.preventDefault();
      e.stopImmediatePropagation();
      submitIngest();
    }, true);
    ingestDialog.addEventListener("close", () => {
      F.ingestItemId = null;
      $("modal-item-ingest-title").textContent = tr("synd.ingest.title");
      $("item-ingest-note").textContent = tr("synd.ingest.note");
    });
  }

  const refresh = $("btn-client-refresh");
  if (refresh) refresh.addEventListener("click", () => { loadFeeds(); if (Feeds.active) loadItems(true); });

  // Keys of the feed view: j and k move in the list, s is the star (the keys of the timeline list).
  document.addEventListener("keydown", (e) => {
    if (!Feeds.active || !F.host || F.host.stage() !== "collect" || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.target.matches("input, textarea, select") || document.querySelector("dialog[open]")) return;
    if (!F.items.length) return;
    const index = F.items.findIndex((item) => String(item.id) === String(F.selectedId));
    if (e.key === "j" || e.key === "k") {
      const next = F.items[e.key === "j" ? Math.min(F.items.length - 1, index + 1) : Math.max(0, index - 1)];
      if (next) selectItem(next.id);
    } else if (e.key === "s" && index >= 0) {
      patchItem(F.items[index].id, { starred: !F.items[index].starred });
    }
  });

  // The poller of the server adds items each hour. The counts load again when the tab becomes visible.
  document.addEventListener("visibilitychange", () => { if (!document.hidden && IS_OWNER && F.feeds !== null) loadFeeds(); });
})();
