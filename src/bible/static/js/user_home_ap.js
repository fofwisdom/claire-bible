/* User Home ActivityPub Profile, Summary, and Conditional Proposal B Controller */
(function () {
  "use strict";

  var tr = function (key, params) {
    if (window.ClaireI18n && typeof window.ClaireI18n.t === "function") {
      return window.ClaireI18n.t(key, params);
    }
    return typeof window.tr === "function" ? window.tr(key, params) : key;
  };

  var esc = function (s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  };

  var icon = function (name) {
    return window.ClaireIcons && typeof window.ClaireIcons.svg === "function" ? window.ClaireIcons.svg(name) : "";
  };

  var state = {
    mode: "workspace",
    username: "",
    actor: null,
    kbSummary: null,
    items: [],
    total: 0,
    nextMaxId: null,
    kind: "all",
    loading: false,
    mobileTab: "timeline",
    loadedKey: ""
  };

  function apiBaseUrl(path) {
    if (typeof window.apiUrl === "function") return window.apiUrl(path);
    var meta = document.querySelector('meta[name="claire-api-base"]');
    var base = (meta && meta.getAttribute("content")) || "";
    if (!base || base === "__CLAIRE_API_BASE__") base = "";
    return base.replace(/\/$/, "") + "/" + path.replace(/^\//, "");
  }

  function formatDate(ts) {
    if (typeof ts !== "number" || !isFinite(ts)) return tr("home.kb_summary.none");
    try {
      return new Date(ts * 1000).toISOString().slice(0, 10);
    } catch (_) {
      return "-";
    }
  }

  function sanitizeHtml(raw) {
    var htmlStr = String(raw || "");
    if (window.DOMPurify && typeof window.DOMPurify.sanitize === "function") {
      return window.DOMPurify.sanitize(htmlStr);
    }
    return htmlStr;
  }

  function closeTopLayer(dlg) {
    if (dlg && dlg.open && typeof dlg.close === "function") dlg.close();
  }

  function bindDialogPolyfills() {
    if (!("commandForElement" in HTMLButtonElement.prototype)) {
      document.addEventListener("click", function (e) {
        var btn = e.target.closest("button[commandfor][command]");
        if (!btn) return;
        var target = document.getElementById(btn.getAttribute("commandfor"));
        if (!target) return;
        var cmd = btn.getAttribute("command");
        if (cmd === "show-modal" && typeof target.showModal === "function") {
          target.showModal();
        } else if (cmd === "close" && target.tagName === "DIALOG") {
          closeTopLayer(target);
        }
      });
    }
    document.querySelectorAll("dialog.uh-dialog").forEach(function (dlg) {
      if (dlg.dataset.uhBound === "1") return;
      dlg.dataset.uhBound = "1";
      if (!("closedBy" in HTMLDialogElement.prototype)) {
        dlg.addEventListener("click", function (event) {
          if (event.target !== dlg) return;
          var rect = dlg.getBoundingClientRect();
          var inside = rect.top <= event.clientY && event.clientY <= rect.top + rect.height && rect.left <= event.clientX && event.clientX <= rect.left + rect.width;
          if (!inside) closeTopLayer(dlg);
        });
      }
    });
  }

  function avatarInitial(actor) {
    var src = (actor.display_name || actor.username || "C").trim();
    return src ? src.slice(0, 2) : "C";
  }

  function currentAuthScope() {
    if (window.ClaireApp && typeof window.ClaireApp.authScope === "string") return window.ClaireApp.authScope;
    if (document.body && document.body.dataset && document.body.dataset.authScope) return document.body.dataset.authScope;
    return typeof window.AUTH_SCOPE === "string" ? window.AUTH_SCOPE : "";
  }

  function renderProfileCard() {
    var host = document.getElementById("home-ap-profile");
    if (!host) return;
    var actor = state.actor;
    var kb = state.kbSummary;
    if (!actor) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.hidden = false;
    var isOwner = state.mode === "workspace" && currentAuthScope() === "owner";
    var discoverChip = actor.discoverable
      ? '<span class="uh-chip kb">' + icon("globe") + esc(tr("home.profile.discoverable")) + "</span>"
      : '<span class="uh-chip warn">' + icon("lock") + esc(tr("home.profile.private_discovery")) + "</span>";
    var kbChip = kb
      ? (kb.is_public
          ? '<span class="uh-chip kb">' + icon("book") + esc(tr("home.profile.kb_public")) + "</span>"
          : '<span class="uh-chip warn">' + icon("lock") + esc(tr("home.profile.kb_private")) + "</span>")
      : "";
    var actions = isOwner
      ? '<button type="button" class="uh-btn pri" id="uh-new-note-btn" commandfor="modal-home-note" command="show-modal">' + icon("edit") + esc(tr("home.profile.new_note")) + "</button>"
        + '<a class="uh-btn" href="/account/profile">' + icon("settings") + esc(tr("home.profile.edit")) + "</a>"
      : '<button type="button" class="uh-btn ap" id="uh-follow-btn" commandfor="modal-home-follow" command="show-modal">' + icon("user-plus") + esc(tr("home.profile.follow")) + "</button>";
    var bioHtml = actor.summary
      ? '<p class="uh-bio">' + esc(actor.summary) + "</p>"
      : (isOwner ? '<p class="uh-bio-empty">' + esc(tr("home.profile.bio_empty")) + "</p>" : "");
    var joinedText = actor.activated_at ? tr("home.profile.joined", { date: formatDate(actor.activated_at) }) : "";

    host.innerHTML =
      '<div class="uh-profile-card">'
      + '<div class="uh-profile-top">'
      + '<div class="uh-identity">'
      + '<div class="uh-avatar" aria-hidden="true">' + esc(avatarInitial(actor)) + "</div>"
      + '<div class="uh-id-main">'
      + '<div class="uh-name-row">'
      + '<h1 class="uh-display-name">' + esc(actor.display_name) + "</h1>"
      + '<span class="uh-chip ap">' + icon("share") + "ActivityPub</span>"
      + discoverChip
      + kbChip
      + "</div>"
      + '<div class="uh-handle-row">'
      + '<code class="uh-handle-code profile-handle">' + esc(actor.handle) + "</code>"
      + '<button type="button" class="uh-btn sm" id="uh-copy-handle-btn" data-uh-copy="' + esc(actor.handle) + '">' + icon("copy") + "<span>" + esc(tr("home.profile.copy_handle")) + "</span></button>"
      + "</div>"
      + "</div>"
      + "</div>"
      + '<div class="uh-profile-actions">' + actions + "</div>"
      + "</div>"
      + bioHtml
      + '<div class="uh-profile-meta">'
      + '<div class="uh-stats-inline">'
      + "<span>" + esc(tr("home.profile.followers", { count: actor.followers_count || 0 })) + "</span>"
      + "<span>" + esc(tr("home.profile.following", { count: actor.following_count || 0 })) + "</span>"
      + "<span>" + esc(tr("home.profile.posts", { count: actor.outbox_count || 0 })) + "</span>"
      + (joinedText ? "<span>" + esc(joinedText) + "</span>" : "")
      + "</div>"
      + "</div>"
      + "</div>";
  }

  function renderKbSummaryCard() {
    if (state.mode !== "standalone") return;
    var el = document.getElementById("uh-private-kb-summary");
    var kb = state.kbSummary;
    if (!el || !kb) return;
    var badge = kb.is_public
      ? '<span class="uh-chip kb">' + esc(tr("home.kb_summary.public")) + "</span>"
      : '<span class="uh-chip warn">' + esc(tr("home.kb_summary.private")) + "</span>";
    var stats = [
      tr("home.kb_summary.documents", { count: kb.documents || 0 }),
      tr("home.kb_summary.entities", { count: kb.entities || 0 }),
      tr("home.kb_summary.relations", { count: kb.relations || 0 }),
      tr("home.kb_summary.last_ingest", { time: formatDate(kb.last_ingest_at) })
    ].join(" · ");
    var labelEl = el.querySelector("[data-uh-kb-label]");
    var badgeEl = el.querySelector("[data-uh-kb-badge]");
    var statsEl = el.querySelector("[data-uh-kb-stats]");
    if (labelEl) labelEl.textContent = kb.label || tr("home.kb_summary.title");
    if (badgeEl) badgeEl.innerHTML = badge;
    if (statsEl) statsEl.textContent = stats;
  }

  function postItemHtml(item) {
    var actor = state.actor || {};
    var isArticle = item.kind === "Article";
    var kindChip = isArticle
      ? '<span class="uh-chip kb">' + icon("book") + esc(tr("home.timeline.kind_article")) + "</span>"
      : '<span class="uh-chip ap">' + icon("comment") + esc(tr("home.timeline.kind_note")) + "</span>";
    var langChip = item.language ? '<span class="uh-chip">' + esc(item.language) + "</span>" : "";
    var editedChip = item.edited_at ? '<span class="uh-chip">' + esc(tr("home.timeline.edited")) + "</span>" : "";

    var bodyHtml = "";
    if (isArticle) {
      var workspaceLink = state.mode === "workspace" && item.document_id
        ? '<button type="button" class="uh-btn sm" data-uh-open-doc="' + esc(item.document_id) + '">' + icon("book") + esc(tr("home.timeline.open_doc")) + "</button>"
        : "";
      var shareLink = item.share_url
        ? '<a class="uh-btn sm" href="' + esc(item.share_url) + '">' + icon("external") + esc(tr("home.timeline.open_share")) + "</a>"
        : "";
      bodyHtml =
        '<div class="uh-article-card">'
        + '<h3 class="uh-article-title">' + esc(item.title || "") + "</h3>"
        + (item.summary_text ? '<p class="uh-article-summary">' + esc(item.summary_text) + "</p>" : "")
        + '<div class="uh-article-links">'
        + (item.source_type ? '<span class="uh-chip">' + esc(item.source_type) + "</span>" : "")
        + workspaceLink
        + shareLink
        + "</div>"
        + "</div>";
    } else {
      var safeContent = '<div class="uh-post-body">' + sanitizeHtml(item.content_html) + "</div>";
      if (item.sensitive) {
        bodyHtml =
          '<details class="uh-cw"><summary>'
          + esc(tr("home.timeline.cw_prefix", { summary: item.cw_summary || "" }))
          + "</summary>" + safeContent + "</details>";
      } else {
        bodyHtml = safeContent;
      }
    }

    var replyHtml = item.in_reply_to
      ? '<div class="uh-quote-box">' + esc(tr("home.timeline.reply_label")) + ": <code>" + esc(item.in_reply_to) + "</code></div>"
      : "";
    var quoteHtml = item.quote && item.quote.iri
      ? '<div class="uh-quote-box' + (item.quote.state === "pending" ? " pending" : "") + '">'
        + esc(tr("home.timeline.quote_label", { state: item.quote.state || "pending" }))
        + ": <code>" + esc(item.quote.iri) + "</code></div>"
      : "";

    return (
      '<article class="uh-post" data-uh-post-id="' + esc(item.id) + '">'
      + '<div class="uh-post-meta">'
      + '<span class="uh-post-author">' + esc(actor.display_name || "") + "</span>"
      + "<span>" + esc(actor.handle || "") + "</span>"
      + "<span>·</span>"
      + "<time>" + esc(formatDate(item.published_at)) + "</time>"
      + editedChip
      + kindChip
      + langChip
      + "</div>"
      + replyHtml
      + bodyHtml
      + quoteHtml
      + '<div class="uh-post-foot">'
      + '<div class="uh-post-stats">'
      + '<span class="uh-post-stat">' + icon("star") + esc(tr("home.timeline.likes", { count: item.likes || 0 })) + "</span>"
      + '<span class="uh-post-stat">' + icon("repeat") + esc(tr("home.timeline.boosts", { count: item.announces || 0 })) + "</span>"
      + '<span class="uh-post-stat">' + icon("quote") + esc(tr("home.timeline.quotes", { count: item.quotes || 0 })) + "</span>"
      + "</div>"
      + '<span class="uh-chip">' + esc(tr("home.timeline.quote_policy", { policy: item.quote_policy || "public" })) + "</span>"
      + "</div>"
      + "</article>"
    );
  }

  function renderTimeline() {
    var host = document.getElementById("home-ap-timeline");
    if (!host) return;
    if (!state.actor) {
      host.hidden = true;
      host.innerHTML = "";
      return;
    }
    host.hidden = false;
    var showArticleFilter = state.kbSummary && state.kbSummary.is_public;
    var filtersHtml =
      '<div class="uh-tl-filters">'
      + '<button type="button" class="uh-filter-btn" data-uh-kind="all" aria-pressed="' + (state.kind === "all" ? "true" : "false") + '">' + esc(tr("home.timeline.filter_all")) + "</button>"
      + '<button type="button" class="uh-filter-btn" data-uh-kind="note" aria-pressed="' + (state.kind === "note" ? "true" : "false") + '">' + esc(tr("home.timeline.filter_note")) + "</button>"
      + (showArticleFilter ? '<button type="button" class="uh-filter-btn" data-uh-kind="article" aria-pressed="' + (state.kind === "article" ? "true" : "false") + '">' + esc(tr("home.timeline.filter_article")) + "</button>" : "")
      + "</div>";

    var feedHtml = state.items.length
      ? state.items.map(postItemHtml).join("")
      : '<div class="uh-tl-empty">' + esc(tr("home.timeline.empty")) + "</div>";

    var footHtml = state.nextMaxId
      ? '<div class="uh-tl-foot"><button type="button" class="uh-btn sm" id="uh-load-more-btn"' + (state.loading ? " disabled" : "") + ">" + esc(state.loading ? tr("home.timeline.loading") : tr("home.timeline.load_more")) + "</button></div>"
      : "";

    host.innerHTML =
      '<section class="uh-tl-card" aria-label="' + esc(tr("home.timeline.title")) + '">'
      + '<div class="uh-tl-head">'
      + '<div class="uh-tl-title-wrap">'
      + '<h2 class="uh-tl-title">' + icon("rss") + esc(tr("home.timeline.title")) + "</h2>"
      + '<span class="uh-chip">' + esc(String(state.total || 0)) + "</span>"
      + "</div>"
      + filtersHtml
      + "</div>"
      + '<div class="uh-tl-feed">' + feedHtml + "</div>"
      + footHtml
      + "</section>";
  }

  function syncFollowDialog() {
    var actor = state.actor;
    if (!actor) return;
    var handleEl = document.getElementById("uh-follow-handle-val");
    var iriEl = document.getElementById("uh-follow-iri-val");
    var domainInput = document.getElementById("uh-remote-domain");
    var previewEl = document.getElementById("uh-remote-follow-url");
    if (handleEl) handleEl.textContent = actor.handle;
    if (iriEl) iriEl.textContent = actor.actor_url;
    if (domainInput && previewEl) {
      var dom = (domainInput.value || "").trim().replace(/^https?:\/\//i, "").replace(/\/.*$/, "");
      previewEl.textContent = dom ? ("https://" + dom + "/authorize_interaction?uri=" + encodeURIComponent(actor.actor_url)) : actor.actor_url;
    }
  }

  function setMobileTab(tabName, moveFocus) {
    state.mobileTab = tabName === "kb" ? "kb" : "timeline";
    var scope = document.querySelector(".home-b-scope");
    if (scope) scope.setAttribute("data-mobile-tab", state.mobileTab);
    var tabTL = document.getElementById("home-tab-timeline");
    var tabKB = document.getElementById("home-tab-kb");
    [tabTL, tabKB].forEach(function (tab) {
      if (!tab) return;
      var active = (tab === tabTL && state.mobileTab === "timeline") || (tab === tabKB && state.mobileTab === "kb");
      tab.setAttribute("aria-selected", active ? "true" : "false");
      tab.toggleAttribute("focusgroupstart", active);
      if (active) {
        if (moveFocus) tab.focus();
      }
    });
    if (state.mobileTab === "kb" && typeof window.drawHomeMiniMap === "function") {
      requestAnimationFrame(window.drawHomeMiniMap);
    }
  }

  function bindMobileTabs() {
    var tablist = document.querySelector("#home-mobile-tabs .uh-tablist");
    var scope = document.querySelector(".home-b-scope");
    if (!tablist || !scope || tablist.dataset.uhBound === "1") return;
    tablist.dataset.uhBound = "1";
    var tabs = Array.prototype.slice.call(tablist.querySelectorAll(".uh-tab"));
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        setMobileTab(tab.id === "home-tab-kb" ? "kb" : "timeline", false);
      });
    });
    if (!Object.hasOwn(HTMLElement.prototype, "focusGroup")) {
      tablist.addEventListener("keydown", function (e) {
        var idx = tabs.indexOf(document.activeElement);
        if (idx === -1) return;
        var next = -1;
        if (e.key === "ArrowRight") next = (idx + 1) % tabs.length;
        else if (e.key === "ArrowLeft") next = (idx - 1 + tabs.length) % tabs.length;
        else if (e.key === "Home") next = 0;
        else if (e.key === "End") next = tabs.length - 1;
        if (next !== -1) {
          e.preventDefault();
          setMobileTab(tabs[next].id === "home-tab-kb" ? "kb" : "timeline", true);
        }
      });
    }
    scope.addEventListener("beforematch", function (e) {
      if (e.target.closest(".home-b-kb")) setMobileTab("kb", false);
      else if (e.target.closest(".home-b-timeline")) setMobileTab("timeline", false);
    });
    if (!CSS.supports("animation-timeline", "scroll()")) {
      var first = tablist.firstElementChild;
      var last = tablist.lastElementChild;
      if (first && last && window.IntersectionObserver) {
        var obs = new IntersectionObserver(function (entries) {
          entries.forEach(function (ent) {
            tablist.classList.toggle(ent.target === first ? "more-start" : "more-end", ent.intersectionRatio < 1);
          });
        }, { root: tablist, threshold: 1, rootMargin: "1px" });
        obs.observe(first);
        obs.observe(last);
      }
    }
  }

  function fetchTimeline(append) {
    if (state.loading) return;
    state.loading = true;
    renderTimeline();
    var params = new URLSearchParams();
    params.set("limit", "20");
    if (state.kind && state.kind !== "all") params.set("kind", state.kind);
    if (append && state.nextMaxId) params.set("max_id", state.nextMaxId);
    fetch(apiBaseUrl("home-timeline?" + params.toString()), { cache: "no-store", credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        state.loading = false;
        if (!data) {
          renderTimeline();
          return;
        }
        state.items = append ? state.items.concat(data.items || []) : (data.items || []);
        state.total = typeof data.total === "number" ? data.total : state.items.length;
        state.nextMaxId = data.next_max_id || null;
        renderTimeline();
      })
      .catch(function () {
        state.loading = false;
        renderTimeline();
      });
  }

  function bindGlobalEvents() {
    if (document.body.dataset.uhEventsBound === "1") return;
    document.body.dataset.uhEventsBound = "1";

    document.addEventListener("click", function (e) {
      var copyBtn = e.target.closest("[data-uh-copy]");
      if (copyBtn) {
        var val = copyBtn.getAttribute("data-uh-copy");
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(val).catch(function () {});
        }
        var labelSpan = copyBtn.querySelector("span");
        if (labelSpan) {
          var prev = labelSpan.textContent;
          labelSpan.textContent = tr("home.profile.copied");
          setTimeout(function () { labelSpan.textContent = prev; }, 1600);
        }
        return;
      }

      var kindBtn = e.target.closest("[data-uh-kind]");
      if (kindBtn) {
        state.kind = kindBtn.getAttribute("data-uh-kind") || "all";
        state.nextMaxId = null;
        fetchTimeline(false);
        return;
      }

      if (e.target.closest("#uh-load-more-btn")) {
        fetchTimeline(true);
        return;
      }

      var docBtn = e.target.closest("[data-uh-open-doc]");
      if (docBtn && typeof window.selectDoc === "function") {
        window.selectDoc(docBtn.getAttribute("data-uh-open-doc"));
        return;
      }

      if (e.target.closest("#uh-follow-btn")) {
        syncFollowDialog();
        return;
      }

      if (e.target.closest("#uh-follow-copy-handle") && state.actor && navigator.clipboard) {
        navigator.clipboard.writeText(state.actor.handle).catch(function () {});
        return;
      }

      if (e.target.closest("#uh-follow-copy-iri") && state.actor && navigator.clipboard) {
        navigator.clipboard.writeText(state.actor.actor_url).catch(function () {});
        return;
      }

      if (e.target.closest("#uh-follow-open-remote")) {
        syncFollowDialog();
        var url = document.getElementById("uh-remote-follow-url");
        if (url && /^https?:\/\//i.test(url.textContent)) {
          window.open(url.textContent, "_blank", "noopener,noreferrer");
        }
      }
    });

    document.addEventListener("input", function (e) {
      if (e.target && e.target.id === "uh-remote-domain") syncFollowDialog();
    });

    var noteForm = document.getElementById("uh-note-form");
    if (noteForm) {
      noteForm.addEventListener("submit", function (e) {
        e.preventDefault();
        var msgEl = document.getElementById("uh-note-msg");
        var submitBtn = document.getElementById("uh-note-submit");
        var textEl = document.getElementById("uh-note-content");
        var cwEl = document.getElementById("uh-note-cw");
        var langEl = document.getElementById("uh-note-lang");
        var qpEl = document.getElementById("uh-note-quote-policy");
        var payload = {
          content: textEl ? textEl.value : "",
          language: langEl ? langEl.value : "ko",
          quote_policy: qpEl ? qpEl.value : "public"
        };
        if (cwEl && cwEl.value.trim()) payload.summary = cwEl.value.trim();
        if (submitBtn) submitBtn.disabled = true;
        if (msgEl) msgEl.textContent = tr("home.note_dlg.publishing");
        fetch("/api/federation/notes", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "Accept": "application/json" },
          body: JSON.stringify(payload)
        })
          .then(function (r) {
            return r.json().catch(function () { return null; }).then(function (body) {
              return { ok: r.ok, body: body };
            });
          })
          .then(function (res) {
            if (submitBtn) submitBtn.disabled = false;
            if (!res.ok) {
              if (msgEl) msgEl.textContent = (res.body && res.body.error) || tr("home.note_dlg.error");
              return;
            }
            if (msgEl) msgEl.textContent = tr("home.note_dlg.published");
            noteForm.reset();
            closeTopLayer(document.getElementById("modal-home-note"));
            state.loadedKey = "";
            fetchWorkspaceHome(true);
          })
          .catch(function () {
            if (submitBtn) submitBtn.disabled = false;
            if (msgEl) msgEl.textContent = tr("home.note_dlg.error");
          });
      });
    }
  }

  function isUserHomeWorkspace(theme) {
    if (theme && theme.is_personal === false) return false;
    var path = window.location.pathname || "/";
    if (/^\/kb\//.test(path)) return false;
    return true;
  }

  function setUserHomeActive(active) {
    var scope = document.querySelector(".home-b-scope");
    var tabsWrap = document.getElementById("home-mobile-tabs");
    if (scope) scope.setAttribute("data-user-home", active ? "1" : "0");
    if (tabsWrap) tabsWrap.hidden = !active;
    if (!active) {
      var p = document.getElementById("home-ap-profile");
      var t = document.getElementById("home-ap-timeline");
      if (p) { p.hidden = true; p.innerHTML = ""; }
      if (t) { t.hidden = true; t.innerHTML = ""; }
    }
  }

  function fetchWorkspaceHome(force, theme) {
    var themeId = (theme && theme.id != null) ? theme.id : (window.activeThemeId || "0");
    var key = String(themeId) + ":" + currentAuthScope();
    if (!force && state.loadedKey === key && state.actor) {
      renderProfileCard();
      renderTimeline();
      return;
    }
    state.loadedKey = key;
    Promise.all([
      fetch(apiBaseUrl("home-profile"), { cache: "no-store", credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; }),
      fetch(apiBaseUrl("home-timeline?limit=20"), { cache: "no-store", credentials: "same-origin" }).then(function (r) { return r.ok ? r.json() : null; }).catch(function () { return null; })
    ]).then(function (results) {
      var prof = results[0];
      var tl = results[1];
      if (!prof || !prof.actor) {
        state.actor = null;
        setUserHomeActive(false);
        return;
      }
      state.actor = prof.actor;
      state.kbSummary = prof.kb_summary || null;
      state.items = (tl && tl.items) || [];
      state.total = (tl && typeof tl.total === "number") ? tl.total : state.items.length;
      state.nextMaxId = (tl && tl.next_max_id) || null;
      setUserHomeActive(true);
      setMobileTab(state.mobileTab, false);
      renderProfileCard();
      renderTimeline();
      syncFollowDialog();
    });
  }

  function refreshWorkspaceHome(theme) {
    state.mode = "workspace";
    bindDialogPolyfills();
    bindMobileTabs();
    bindGlobalEvents();
    if (!isUserHomeWorkspace(theme)) {
      state.actor = null;
      setUserHomeActive(false);
      return;
    }
    setUserHomeActive(true);
    fetchWorkspaceHome(false, theme);
  }

  function bootStandalone() {
    var el = document.getElementById("uh-bootstrap");
    if (!el || !el.textContent) return;
    try {
      var data = JSON.parse(el.textContent);
      state.mode = "standalone";
      state.actor = data.actor || null;
      state.kbSummary = data.kb_summary || null;
      state.items = (data.timeline && data.timeline.items) || [];
      state.total = (data.timeline && typeof data.timeline.total === "number") ? data.timeline.total : state.items.length;
      state.nextMaxId = (data.timeline && data.timeline.next_max_id) || null;
      bindDialogPolyfills();
      bindMobileTabs();
      bindGlobalEvents();
      setUserHomeActive(Boolean(state.actor));
      setMobileTab("timeline", false);
      renderProfileCard();
      renderKbSummaryCard();
      renderTimeline();
      syncFollowDialog();
    } catch (_) {}
  }

  window.ClaireUserHome = {
    refreshWorkspaceHome: refreshWorkspaceHome,
    bootStandalone: bootStandalone,
    setMobileTab: setMobileTab
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      if (document.getElementById("uh-bootstrap")) bootStandalone();
    });
  } else if (document.getElementById("uh-bootstrap")) {
    bootStandalone();
  }
})();
