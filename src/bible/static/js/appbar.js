(function () {
  "use strict";

  function syncThemeIcons(theme) {
    var nextIcon = theme === "dark" ? "sun" : "moon";
    var btn = document.getElementById("themebtn");
    if (!btn) return;
    var iconEl = btn.querySelector("[data-icon]") || btn;
    iconEl.setAttribute("data-icon", nextIcon);
    if (window.ClaireIcons && typeof window.ClaireIcons.hydrate === "function") {
      window.ClaireIcons.hydrate(iconEl);
    }
  }

  function applyTheme(next) {
    var val = next === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", val);
    try { localStorage.setItem("claireTheme", val); } catch (_) {}
    syncThemeIcons(val);
    if (typeof window.__CLAIRE_ON_THEME_CHANGE === "function") {
      try { window.__CLAIRE_ON_THEME_CHANGE(val); } catch (_) {}
    }
    return val;
  }

  function toggleTheme() {
    var cur = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    return applyTheme(cur === "dark" ? "light" : "dark");
  }

  function supportsNativeAnchorPos() {
    return typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("position-area", "block-end");
  }

  function positionMenuFallback(trigger, menu, fallbackW, fallbackH) {
    if (!trigger || !menu || supportsNativeAnchorPos()) return;
    var r = trigger.getBoundingClientRect();
    var mW = menu.offsetWidth || fallbackW || 220;
    var mH = menu.offsetHeight || fallbackH || 160;
    var top = r.bottom + 6;
    if (top + mH > window.innerHeight - 8 && r.top - 6 - mH >= 8) {
      top = r.top - 6 - mH;
    }
    var left = menu.classList.contains("app-popover-end") ? (r.right - mW) : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - mW - 8));
    menu.style.top = Math.round(top) + "px";
    menu.style.left = Math.round(left) + "px";
  }

  var supportsDisplayTransition;
  function canTransitionDisplay() {
    if (supportsDisplayTransition !== undefined) return supportsDisplayTransition;
    if (typeof CSS === "undefined" || typeof CSS.supports !== "function" || !CSS.supports("transition-behavior", "allow-discrete") || !document.body) {
      return false;
    }
    var probe = document.createElement("div");
    probe.style.cssText = "transition: display 1s allow-discrete !important; display: block;";
    document.body.appendChild(probe);
    getComputedStyle(probe).display;
    probe.style.display = "none";
    supportsDisplayTransition = getComputedStyle(probe).display === "block";
    probe.remove();
    return supportsDisplayTransition;
  }

  function supportsTopLayerExit() {
    return typeof CSS !== "undefined" && typeof CSS.supports === "function" && CSS.supports("overlay", "auto") && canTransitionDisplay();
  }

  function closeTopLayer(element) {
    if (!element) return;
    if (!supportsTopLayerExit()) {
      element.setAttribute("data-closing", "");
      var animations = typeof element.getAnimations === "function" ? element.getAnimations({ subtree: true }) : [];
      var finishPromise = animations.length > 0
        ? Promise.race([Promise.allSettled(animations.map(function (a) { return a.finished; })), new Promise(function (r) { setTimeout(r, 400); })])
        : Promise.resolve();
      finishPromise.then(function () {
        if (!element.hasAttribute("data-closing")) return;
        element.removeAttribute("data-closing");
        try { element.hidePopover(); } catch (_) {}
      });
      return;
    }
    try { element.hidePopover(); } catch (_) {}
  }

  function closePopovers(except) {
    document.querySelectorAll('.app-popover[popover]').forEach(function (pop) {
      if (pop === except) return;
      try {
        if (pop.matches(":popover-open") || pop.classList.contains(":popover-open")) pop.hidePopover();
      } catch (_) {}
    });
  }

  function bindPopoverMenu(trigger) {
    if (!trigger || trigger.dataset.appbarMenuBound === "1") return;
    var targetId = trigger.getAttribute("commandfor");
    var pop = targetId ? document.getElementById(targetId) : null;
    if (!pop) return;
    trigger.dataset.appbarMenuBound = "1";

    var group = pop.querySelector('[focusgroup~="menu"]');
    var supportsInvoker = "commandForElement" in HTMLButtonElement.prototype;
    var supportsFocusGroup = "focusGroup" in HTMLElement.prototype;
    var supportsAnchor = supportsNativeAnchorPos();
    var pointerPressed = false;
    var enterNextOpen = false;

    function visibleItems() {
      if (!group) return [];
      return Array.prototype.slice.call(group.children).filter(function (el) {
        if (!el.matches("button, a")) return false;
        if (el.hidden || el.disabled || el.getAttribute("aria-disabled") === "true") return false;
        return el.getClientRects().length > 0 && getComputedStyle(el).display !== "none";
      });
    }

    function isPopOpen() {
      try { return pop.matches(":popover-open") || pop.classList.contains(":popover-open"); } catch (_) { return false; }
    }

    trigger.addEventListener("pointerdown", function () {
      pointerPressed = true;
    });

    trigger.addEventListener("keydown", function (ev) {
      if (ev.key === "ArrowDown") {
        ev.preventDefault();
        enterNextOpen = true;
        if (!isPopOpen()) {
          if (supportsInvoker) trigger.click();
          else {
            try { pop.showPopover({ source: trigger }); } catch (_) { try { pop.showPopover(); } catch (__) {} }
          }
        } else {
          var list = visibleItems();
          if (list.length) list[0].focus();
        }
      } else if (ev.key === "Enter" || ev.key === " ") {
        pointerPressed = false;
        enterNextOpen = true;
      }
    });

    if (!supportsInvoker) {
      trigger.addEventListener("click", function (ev) {
        ev.preventDefault();
        if (pointerPressed && ev.detail === 0) pointerPressed = false;
        if (isPopOpen()) {
          try { pop.hidePopover(); } catch (_) {}
          return;
        }
        if (!pointerPressed) enterNextOpen = true;
        pointerPressed = false;
        try { pop.showPopover({ source: trigger }); } catch (_) { try { pop.showPopover(); } catch (__) {} }
      });
    } else {
      trigger.addEventListener("click", function (ev) {
        if (ev.detail === 0) enterNextOpen = true;
        pointerPressed = false;
      });
    }

    pop.addEventListener("toggle", function (ev) {
      var opened = ev.newState === "open";
      trigger.setAttribute("aria-expanded", opened ? "true" : "false");
      if (opened) {
        closePopovers(pop);
        if (!supportsAnchor) {
          positionMenuFallback(trigger, pop);
        }
        if (enterNextOpen) {
          enterNextOpen = false;
          var list = visibleItems();
          if (list.length) list[0].focus();
        }
      } else {
        enterNextOpen = false;
      }
    });

    pop.addEventListener("click", function (ev) {
      var item = ev.target && ev.target.closest("a, button");
      if (!item || !pop.contains(item) || item.disabled) return;
      if (item.closest("form")) return;
      if (item.tagName === "A") {
        try { pop.hidePopover(); } catch (_) {}
        return;
      }
      try { pop.hidePopover(); } catch (_) {}
    });

    if (group && !supportsFocusGroup) {
      group.setAttribute("role", "menu");
      Array.prototype.forEach.call(group.children, function (el) {
        if (el.matches("button, a") && !el.hasAttribute("role")) el.setAttribute("role", "menuitem");
      });
    }

    if (group) {
      group.addEventListener("keydown", function (ev) {
        if (ev.key === "Tab") {
          try { pop.hidePopover(); } catch (_) {}
          return;
        }
        if (ev.key === "Escape") {
          ev.stopPropagation();
          try { pop.hidePopover(); } catch (_) {}
          trigger.focus();
          return;
        }
        if (!supportsFocusGroup && ["ArrowDown", "ArrowUp", "Home", "End"].indexOf(ev.key) !== -1) {
          var list = visibleItems();
          if (!list.length) return;
          var idx = list.indexOf(document.activeElement);
          var nextIdx = 0;
          if (ev.key === "Home") nextIdx = 0;
          else if (ev.key === "End") nextIdx = list.length - 1;
          else if (ev.key === "ArrowUp") nextIdx = idx <= 0 ? list.length - 1 : idx - 1;
          else nextIdx = idx < 0 || idx >= list.length - 1 ? 0 : idx + 1;
          ev.preventDefault();
          list[nextIdx].focus();
        }
      });
    }
  }

  function installClosedByFallback() {
    if ("closedBy" in HTMLDialogElement.prototype) return;
    document.querySelectorAll('dialog[closedby="any"]').forEach(function (dlg) {
      if (dlg.dataset.closedbyBound === "1") return;
      dlg.dataset.closedbyBound = "1";
      var pressedOutside = false;
      function isOutside(ev) {
        var r = dlg.getBoundingClientRect();
        return ev.clientX < r.left || ev.clientX > r.right || ev.clientY < r.top || ev.clientY > r.bottom;
      }
      dlg.addEventListener("pointerdown", function (ev) { pressedOutside = isOutside(ev); });
      dlg.addEventListener("pointerup", function (ev) { if (pressedOutside && isOutside(ev)) dlg.close(); pressedOutside = false; });
    });
  }

  // --- Notification bell (docs/design/PWA_NOTIFICATION_DESIGN.md PN4 rule 2) ---
  // The bell shows for an owner session only: the list request gives 404 for each other caller, and the bell then stays hidden.
  var NOTIFY_PATH = "/api/me/notifications";
  var NOTIFY_POLL_MS = 60000;
  var notify = { started: false, timer: null, busy: false, items: [] };

  function tr(key, params) {
    return window.ClaireI18n ? window.ClaireI18n.t(key, params) : key;
  }

  function notifyTimeText(seconds) {
    var delta = Math.round(seconds - Date.now() / 1000);
    var abs = Math.abs(delta);
    var unit = abs < 3600 ? "minute" : abs < 86400 ? "hour" : "day";
    var size = unit === "minute" ? 60 : unit === "hour" ? 3600 : 86400;
    try {
      return new Intl.RelativeTimeFormat(document.documentElement.lang || "en", { numeric: "auto" }).format(Math.round(delta / size), unit);
    } catch (_) {
      return new Date(seconds * 1000).toLocaleString();
    }
  }

  function setNotifyCount(unread) {
    var btn = document.getElementById("notifybtn");
    var badge = document.getElementById("notify-count");
    if (!btn || !badge) return;
    var count = typeof unread === "number" && unread > 0 ? unread : 0;
    badge.textContent = count > 99 ? "99+" : String(count);
    badge.hidden = count === 0;
    var label = count ? tr("nav.notify.open_unread", { count: count }) : tr("nav.notify.open");
    btn.title = label;
    btn.setAttribute("aria-label", label);
    var readAll = document.getElementById("notify-read-all");
    if (readAll) readAll.hidden = count === 0;
    try {
      if (count && navigator.setAppBadge) navigator.setAppBadge(count).catch(function () {});
      else if (!count && navigator.clearAppBadge) navigator.clearAppBadge().catch(function () {});
    } catch (_) {}
  }

  function renderNotifyList(items) {
    var list = document.getElementById("notify-list");
    var empty = document.getElementById("notify-empty");
    if (!list || !empty) return;
    notify.items = items;
    list.textContent = "";
    empty.hidden = items.length > 0;
    items.forEach(function (item) {
      // Each value is text of the server or of another server: it goes into the page as a text node only.
      var row = document.createElement("a");
      row.className = "notify-item" + (item.read ? " is-read" : "");
      row.setAttribute("role", "listitem");
      row.href = typeof item.url === "string" && item.url.charAt(0) === "/" && item.url.charAt(1) !== "/" ? item.url : "/account/notifications";
      row.dataset.notifyId = String(item.id);
      var dot = document.createElement("span");
      dot.className = "notify-item-dot";
      dot.setAttribute("aria-hidden", "true");
      var title = document.createElement("span");
      title.className = "notify-item-title";
      title.textContent = item.title || "";
      var body = document.createElement("span");
      body.className = "notify-item-body";
      body.textContent = item.body || "";
      var meta = document.createElement("span");
      meta.className = "notify-item-meta";
      meta.textContent = notifyTimeText(item.updated_at);
      row.appendChild(dot);
      row.appendChild(title);
      row.appendChild(body);
      row.appendChild(meta);
      list.appendChild(row);
    });
  }

  function stopNotifications() {
    if (notify.timer) { clearInterval(notify.timer); notify.timer = null; }
    var btn = document.getElementById("notifybtn");
    if (btn) btn.hidden = true;
  }

  function loadNotifications() {
    if (notify.busy) return Promise.resolve();
    notify.busy = true;
    return fetch(NOTIFY_PATH + "?limit=20", { credentials: "same-origin", headers: { Accept: "application/json" } }).then(function (res) {
      if (res.status === 401 || res.status === 403 || res.status === 404) { stopNotifications(); return null; }
      if (!res.ok) throw new Error("http_" + res.status);
      return res.json();
    }).then(function (state) {
      if (!state) return;
      var btn = document.getElementById("notifybtn");
      if (btn) btn.hidden = false;
      renderNotifyList(Array.isArray(state.items) ? state.items : []);
      setNotifyCount(state.unread);
    }).catch(function () {
      var empty = document.getElementById("notify-empty");
      var list = document.getElementById("notify-list");
      if (empty && list && !list.children.length) { empty.textContent = tr("nav.notify.load_failed"); empty.hidden = false; }
    }).then(function () { notify.busy = false; });
  }

  function markNotificationsRead(body) {
    return fetch(NOTIFY_PATH + "/read", { method: "POST", credentials: "same-origin", keepalive: true, headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify(body) }).then(function (res) {
      return res.ok ? res.json() : null;
    }).then(function (state) {
      if (state && typeof state.unread === "number") setNotifyCount(state.unread);
    }).catch(function () {});
  }

  function startNotifications() {
    var btn = document.getElementById("notifybtn");
    var pop = document.getElementById("pop-notify");
    if (!btn || !pop || notify.started) return;
    notify.started = true;
    pop.addEventListener("toggle", function (ev) { if (ev.newState === "open") loadNotifications(); });
    pop.addEventListener("click", function (ev) {
      var row = ev.target && ev.target.closest ? ev.target.closest(".notify-item") : null;
      if (row && pop.contains(row)) {
        // The request has keepalive, thus it finishes while the browser goes to the entry address.
        if (!row.classList.contains("is-read")) markNotificationsRead({ ids: [Number(row.dataset.notifyId)] });
        return;
      }
      if (ev.target && ev.target.id === "notify-read-all") {
        markNotificationsRead({ all: true }).then(loadNotifications);
      }
    });
    document.addEventListener("visibilitychange", function () { if (!document.hidden) loadNotifications(); });
    if (navigator.serviceWorker) {
      navigator.serviceWorker.addEventListener("message", function (ev) {
        if (ev.data && ev.data.type === "claire-notification") loadNotifications();
      });
    }
    notify.timer = setInterval(function () { if (!document.hidden) loadNotifications(); }, NOTIFY_POLL_MS);
    loadNotifications();
  }

  // --- Ingest Queue quick panel (Option A ledger + Option C compact) ---
  var QUEUE_PATH = "/api/me/ingest-queue";
  var QUEUE_VIEW_KEY = "claireQueueViewMode";
  var QUEUE_ACTIVE_POLL_MS = 4000;
  var QUEUE_IDLE_POLL_MS = 20000;
  var QUEUE_STAGES = ["fetch", "extract", "resolve", "detail", "done"];
  var QUEUE_STAGE_INDEX = { init: 0, fetch: 0, extract: 1, resolve: 2, detail: 3, done: 4 };

  function loadQueueViewMode() {
    try {
      return localStorage.getItem(QUEUE_VIEW_KEY) === "compact" ? "compact" : "ledger";
    } catch (_) {
      return "ledger";
    }
  }

  function saveQueueViewMode(mode) {
    try {
      localStorage.setItem(QUEUE_VIEW_KEY, mode === "compact" ? "compact" : "ledger");
    } catch (_) {}
  }

  var queue = {
    started: false,
    stopped: false,
    timer: null,
    busy: false,
    pendingReload: false,
    inflight: null,
    viewMode: loadQueueViewMode(),
    tab: "all",
    expandedCompactId: null,
    loadFailed: false,
    data: { max_slots: 4, busy_slots: 0, purge_allowed: true, counts: { running: 0, queued: 0, staged: 0, error: 0, done: 0 }, items: [] }
  };

  function isQueueOpen() {
    var pop = document.getElementById("pop-queue");
    if (!pop) return false;
    try { return pop.matches(":popover-open") || pop.classList.contains(":popover-open"); } catch (_) { return false; }
  }

  function positionQueueFallback() {
    var btn = document.getElementById("queuebtn");
    var pop = document.getElementById("pop-queue");
    if (!btn || !pop) return;
    positionMenuFallback(btn, pop, 480, 460);
  }

  function announceQueue(msg) {
    var region = document.getElementById("queue-live-region");
    if (!region || !msg) return;
    region.textContent = "";
    window.setTimeout(function () { region.textContent = msg; }, 20);
  }

  function hydrateQueueIcons(root) {
    if (window.ClaireIcons && typeof window.ClaireIcons.hydrate === "function") {
      window.ClaireIcons.hydrate(root);
    }
  }

  function makeQueueEl(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function makeQueueIcon(name) {
    var span = document.createElement("span");
    span.setAttribute("data-icon", name);
    return span;
  }

  function makeQueueChip(text, cls, iconName) {
    var chip = makeQueueEl("span", "q-chip" + (cls ? " " + cls : ""));
    if (iconName) chip.appendChild(makeQueueIcon(iconName));
    chip.appendChild(document.createTextNode(text));
    return chip;
  }

  function validRelativeUrl(url) {
    return typeof url === "string" && url.charAt(0) === "/" && url.charAt(1) !== "/";
  }

  function queueStageIdx(item) {
    if (typeof item.stage_index === "number" && !isNaN(item.stage_index)) {
      return Math.max(0, Math.min(4, Math.floor(item.stage_index)));
    }
    var st = String(item.stage || "").toLowerCase();
    return Object.prototype.hasOwnProperty.call(QUEUE_STAGE_INDEX, st) ? QUEUE_STAGE_INDEX[st] : 0;
  }

  function queueStageLabel(stageKey) {
    return tr("nav.queue.stage_" + stageKey);
  }

  function queueSourceMeta(source) {
    var raw = String(source || "web").toLowerCase();
    if (raw === "telegram" || raw === "bot") return { label: tr("nav.queue.source_telegram"), icon: "send" };
    if (raw === "feed" || raw === "rss" || raw === "federation") return { label: tr("nav.queue.source_feed"), icon: "rss" };
    if (raw === "cli") return { label: tr("nav.queue.source_cli"), icon: "doc" };
    if (raw === "video" || raw === "youtube" || raw === "stt") return { label: tr("nav.queue.source_video"), icon: "film" };
    if (raw === "mcp" || raw === "agent" || raw === "client") return { label: tr("nav.queue.source_mcp"), icon: "bot" };
    if (raw === "file") return { label: tr("nav.queue.source_web"), icon: "doc" };
    if (raw === "web" || raw === "url" || raw === "text") return { label: tr("nav.queue.source_web"), icon: "globe" };
    return { label: String(source), icon: "globe" };
  }

  function queueStatusIcon(status) {
    if (status === "running") return "pulse";
    if (status === "queued") return "clock";
    if (status === "staged") return "bot";
    if (status === "error") return "warning";
    return "check-circle";
  }

  function queueStatusLabel(item) {
    if (item.status === "running") {
      var stKey = QUEUE_STAGES[queueStageIdx(item)] || "fetch";
      return tr("nav.queue.status_running", { stage: queueStageLabel(stKey) });
    }
    if (item.status === "queued") {
      if (item.cancel_queued) return tr("nav.queue.cancel_queued_badge");
      return tr("nav.queue.status_queued", { order: item.order != null ? item.order : 1 });
    }
    if (item.status === "staged") return tr("nav.queue.status_staged");
    if (item.status === "error") return tr("nav.queue.status_error");
    return tr("nav.queue.status_done");
  }

  function queueCounts() {
    var items = Array.isArray(queue.data.items) ? queue.data.items : [];
    var base = { running: 0, queued: 0, staged: 0, error: 0, done: 0 };
    items.forEach(function (it) {
      if (it && Object.prototype.hasOwnProperty.call(base, it.status)) base[it.status] += 1;
    });
    var c = queue.data.counts;
    if (c && typeof c === "object") {
      ["running", "queued", "staged", "error", "done"].forEach(function (k) {
        if (typeof c[k] === "number" && c[k] >= 0) base[k] = c[k];
      });
    }
    return base;
  }

  function queueFilteredItems() {
    var items = Array.isArray(queue.data.items) ? queue.data.items : [];
    if (queue.tab === "active") return items.filter(function (i) { return i.status === "running" || i.status === "queued"; });
    if (queue.tab === "staged") return items.filter(function (i) { return i.status === "staged"; });
    if (queue.tab === "history") return items.filter(function (i) { return i.status === "done" || i.status === "error"; });
    if (queue.tab === "running") return items.filter(function (i) { return i.status === "running"; });
    if (queue.tab === "queued") return items.filter(function (i) { return i.status === "queued"; });
    if (queue.tab === "error") return items.filter(function (i) { return i.status === "error"; });
    if (queue.tab === "done") return items.filter(function (i) { return i.status === "done"; });
    return items;
  }

  function attachQueueScrollFallback() {
    var scroller = document.getElementById("queue-scroll");
    if (!scroller) return;
    if (typeof CSS !== "undefined" && typeof CSS.supports === "function" && (CSS.supports("container-type", "scroll-state") || CSS.supports("animation-timeline", "scroll()"))) {
      return;
    }
    if (!window.IntersectionObserver) return;
    if (scroller.dataset.ioBound === "1") return;
    scroller.dataset.ioBound = "1";
    var start = makeQueueEl("div", "queue-scroll-sentinel");
    var end = makeQueueEl("div", "queue-scroll-sentinel");
    start.setAttribute("aria-hidden", "true");
    end.setAttribute("aria-hidden", "true");
    scroller.prepend(start);
    scroller.append(end);
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.target === start) scroller.classList.toggle("more-start", !entry.isIntersecting);
        if (entry.target === end) scroller.classList.toggle("more-end", !entry.isIntersecting);
      });
    }, { root: scroller });
    io.observe(start);
    io.observe(end);
  }

  function retryQueueItem(item, btn) {
    if (!item || item.inbox_id == null) return;
    if (btn) {
      btn.disabled = true;
      btn.textContent = tr("nav.queue.retrying");
    }
    fetch(QUEUE_PATH + "/" + encodeURIComponent(String(item.inbox_id)) + "/retry", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" }
    }).then(function (res) {
      if (!res.ok) throw new Error("http_" + res.status);
      announceQueue(tr("nav.queue.retry_queued"));
      return loadIngestQueue();
    }).catch(function () {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "";
        btn.appendChild(makeQueueIcon("repeat"));
        btn.appendChild(document.createTextNode(tr("nav.queue.retry")));
        hydrateQueueIcons(btn);
      }
      announceQueue(tr("nav.queue.retry_failed"));
      loadIngestQueue();
    });
  }

  var cancelModal = { action: "", item: null, dryRunData: null, dryRunFailed: false, submitting: false, seq: 0, wasQueueOpen: false };

  function makeQueueBtn(label, iconName, cls, actionName, item) {
    var btn = makeQueueEl("button", "q-btn" + (cls ? " " + cls : ""));
    btn.type = "button";
    if (actionName) btn.setAttribute("data-queue-action", actionName);
    if (iconName) btn.appendChild(makeQueueIcon(iconName));
    btn.appendChild(document.createTextNode(label));
    if (actionName) {
      btn.addEventListener("click", function () { openQueueCancelModal(actionName, item); });
    }
    return btn;
  }

  function appendQueueItemActions(container, item, st) {
    if (!container || !item) return;
    if (st === "running") {
      if (!item.cancel_requested && item.can_cancel) {
        container.appendChild(makeQueueBtn(tr("nav.queue.cancel_running"), "block", "", "cancel-running", item));
      }
      return;
    }
    if (st === "queued") {
      if (item.can_cancel && item.inbox_id != null) {
        container.appendChild(makeQueueBtn(tr("nav.queue.cancel_btn"), "trash", "", "cancel-inbox", item));
      }
      return;
    }
    if (st === "staged") {
      if (item.can_cancel && item.inbox_id != null) {
        container.appendChild(makeQueueBtn(tr("nav.queue.cancel_btn"), "trash", "", "cancel-staged", item));
      }
      return;
    }
    if (st === "error") {
      if (item.can_retry && item.inbox_id != null) {
        var retryBtn = makeQueueEl("button", "q-btn q-btn-danger");
        retryBtn.type = "button";
        retryBtn.appendChild(makeQueueIcon("repeat"));
        retryBtn.appendChild(document.createTextNode(tr("nav.queue.retry")));
        retryBtn.addEventListener("click", function () { retryQueueItem(item, retryBtn); });
        container.appendChild(retryBtn);
      }
      if (item.can_cancel && item.inbox_id != null) {
        container.appendChild(makeQueueBtn(tr("nav.queue.cancel_btn"), "trash", "", "cancel-inbox", item));
      }
      return;
    }
    if (st === "done") {
      if (validRelativeUrl(item.doc_url)) {
        var openLnk = makeQueueEl("a", "q-btn");
        openLnk.href = item.doc_url;
        openLnk.appendChild(document.createTextNode(tr("nav.queue.open_doc")));
        openLnk.appendChild(makeQueueIcon("external"));
        container.appendChild(openLnk);
      }
      if (item.can_purge && item.inbox_id != null) {
        container.appendChild(makeQueueBtn(tr("nav.queue.purge_btn"), "flame", "q-btn-danger", "purge-done", item));
      }
    }
  }

  function ensureCancelDialog() {
    var dlg = document.getElementById("modal-queue-cancel");
    if (dlg) return dlg;
    dlg = document.createElement("dialog");
    dlg.id = "modal-queue-cancel";
    dlg.setAttribute("closedby", "any");
    dlg.className = "claire-dialog queue-cancel-dialog";
    dlg.setAttribute("aria-labelledby", "queue-cancel-title");

    var body = makeQueueEl("div", "claire-dialog-body");
    var head = makeQueueEl("div", "claire-dialog-head");
    var title = makeQueueEl("h2", "", "");
    title.id = "queue-cancel-title";
    var closeBtn = makeQueueEl("button", "claire-dialog-close");
    closeBtn.type = "button";
    closeBtn.id = "queue-cancel-close";
    closeBtn.title = tr("nav.queue.modal_close");
    closeBtn.setAttribute("aria-label", tr("nav.queue.modal_close"));
    closeBtn.appendChild(makeQueueIcon("close"));
    closeBtn.addEventListener("click", function () { dlg.close(); });
    head.appendChild(title);
    head.appendChild(closeBtn);
    body.appendChild(head);

    var target = makeQueueEl("div", "queue-cancel-target");
    target.id = "queue-cancel-target";
    body.appendChild(target);

    var intro = makeQueueEl("p", "claire-dialog-intro");
    intro.id = "queue-cancel-intro";
    body.appendChild(intro);

    var warn = makeQueueEl("div", "queue-cancel-warn");
    warn.id = "queue-cancel-warn";
    warn.hidden = true;
    body.appendChild(warn);

    var info = makeQueueEl("div", "queue-cancel-info");
    info.id = "queue-cancel-info";
    info.hidden = true;
    body.appendChild(info);

    var impact = makeQueueEl("div", "queue-impact-grid");
    impact.id = "queue-cancel-impact";
    impact.hidden = true;
    body.appendChild(impact);

    var tombRow = makeQueueEl("label", "claire-dialog-check");
    tombRow.id = "queue-cancel-tombstone-row";
    var tombChk = document.createElement("input");
    tombChk.type = "checkbox";
    tombChk.id = "queue-cancel-tombstone";
    var tombLbl = document.createElement("span");
    tombLbl.id = "queue-cancel-tombstone-label";
    tombLbl.textContent = tr("nav.queue.modal_tombstone_label");
    tombRow.appendChild(tombChk);
    tombRow.appendChild(document.createTextNode(" "));
    tombRow.appendChild(tombLbl);
    body.appendChild(tombRow);

    var forceRow = makeQueueEl("label", "claire-dialog-check");
    forceRow.id = "queue-cancel-force-row";
    forceRow.hidden = true;
    var forceChk = document.createElement("input");
    forceChk.type = "checkbox";
    forceChk.id = "queue-cancel-force";
    var forceLbl = document.createElement("span");
    forceLbl.id = "queue-cancel-force-label";
    forceLbl.textContent = tr("nav.queue.modal_force_label");
    forceRow.appendChild(forceChk);
    forceRow.appendChild(document.createTextNode(" "));
    forceRow.appendChild(forceLbl);
    forceChk.addEventListener("change", syncCancelConfirmState);
    body.appendChild(forceRow);

    var msg = makeQueueEl("p", "claire-dialog-msg");
    msg.id = "queue-cancel-msg";
    msg.setAttribute("role", "status");
    msg.setAttribute("aria-live", "polite");
    body.appendChild(msg);

    var foot = makeQueueEl("div", "claire-dialog-foot");
    var dismissBtn = document.createElement("button");
    dismissBtn.type = "button";
    dismissBtn.id = "queue-cancel-dismiss";
    dismissBtn.textContent = tr("nav.queue.modal_close");
    dismissBtn.addEventListener("click", function () { dlg.close(); });
    var confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.id = "queue-cancel-confirm";
    confirmBtn.addEventListener("click", submitQueueCancelModal);
    foot.appendChild(dismissBtn);
    foot.appendChild(confirmBtn);
    body.appendChild(foot);

    dlg.appendChild(body);
    dlg.addEventListener("close", function () {
      cancelModal.submitting = false;
      if (cancelModal.wasQueueOpen && !isQueueOpen()) {
        cancelModal.wasQueueOpen = false;
        var pop = document.getElementById("pop-queue");
        var btn = document.getElementById("queuebtn");
        if (pop) {
          try { pop.showPopover({ source: btn || undefined }); } catch (_) { try { pop.showPopover(); } catch (__) {} }
        }
      }
    });
    document.body.appendChild(dlg);
    installClosedByFallback();
    hydrateQueueIcons(dlg);
    return dlg;
  }

  function setCancelMsg(text, isError) {
    var msg = document.getElementById("queue-cancel-msg");
    if (!msg) return;
    msg.textContent = text || "";
    if (!text) msg.removeAttribute("data-state");
    else msg.setAttribute("data-state", isError ? "error" : "ok");
  }

  function setCancelBanner(elId, iconName, text) {
    var el = document.getElementById(elId);
    if (!el) return;
    el.textContent = "";
    if (!text) {
      el.hidden = true;
      return;
    }
    el.appendChild(makeQueueIcon(iconName));
    el.appendChild(makeQueueEl("span", "", text));
    el.hidden = false;
    hydrateQueueIcons(el);
  }

  function setCancelWarn(text) {
    setCancelBanner("queue-cancel-warn", "warning", text);
  }

  function setCancelInfo(text) {
    setCancelBanner("queue-cancel-info", "clock", text);
  }

  function renderCancelImpact(cells) {
    var impact = document.getElementById("queue-cancel-impact");
    if (!impact) return;
    impact.textContent = "";
    if (!cells || !cells.length) {
      impact.hidden = true;
      return;
    }
    cells.forEach(function (c) {
      var box = makeQueueEl("div", "queue-impact-cell");
      box.appendChild(makeQueueEl("strong", "tnum", String(c.value != null ? c.value : 0)));
      box.appendChild(makeQueueEl("span", "", c.label));
      impact.appendChild(box);
    });
    impact.hidden = false;
  }

  function cancelConfirmLabel(action) {
    if (action === "purge-done") return tr("nav.queue.modal_confirm_purge");
    if (action === "cancel-running") return tr("nav.queue.modal_confirm_running");
    return tr("nav.queue.modal_confirm_cancel");
  }

  function syncCancelConfirmState() {
    var confirmBtn = document.getElementById("queue-cancel-confirm");
    var forceRow = document.getElementById("queue-cancel-force-row");
    var forceChk = document.getElementById("queue-cancel-force");
    if (!confirmBtn) return;
    var needsForce = forceRow && !forceRow.hidden;
    var disabled = Boolean(cancelModal.submitting || cancelModal.dryRunFailed || (needsForce && !(forceChk && forceChk.checked)));
    confirmBtn.disabled = disabled;
    confirmBtn.textContent = cancelModal.submitting ? tr("nav.queue.modal_submitting") : cancelConfirmLabel(cancelModal.action);
  }

  function localizedCancelError(status, data) {
    var code = data && typeof data.error === "string" ? data.error : "";
    if (status === 409 && code === "staged_claim_active") return tr("nav.queue.err_staged_claim_active");
    if (status === 409 && code === "job_already_finished") return tr("nav.queue.err_job_already_finished");
    if (status === 409 && code === "item_currently_running") return tr("nav.queue.err_item_currently_running");
    if (status === 409 && code === "cancel_already_queued") return tr("nav.queue.err_cancel_already_queued");
    if (status === 403 && code === "purge_disabled_by_policy") return tr("nav.queue.err_purge_disabled");
    return tr("nav.queue.err_cancel_failed", { error: code || ("HTTP " + status) });
  }

  function applyDryRunResponse(data) {
    if (!data) return;
    cancelModal.dryRunData = data;
    var action = cancelModal.action;
    var item = cancelModal.item || {};
    var forceRow = document.getElementById("queue-cancel-force-row");
    if (data.will_queue === true) {
      setCancelInfo(tr("nav.queue.modal_will_queue_note"));
    }
    if (action === "purge-done") {
      renderCancelImpact([
        { label: tr("nav.queue.modal_impact_docs"), value: data.purged_count },
        { label: tr("nav.queue.modal_impact_orphan_entities"), value: data.orphan_entities_count },
        { label: tr("nav.queue.modal_impact_shared_entities"), value: data.shared_entities_count },
        { label: tr("nav.queue.modal_impact_relations"), value: data.orphan_relations_count },
        { label: tr("nav.queue.modal_impact_files"), value: data.disk_files_count }
      ]);
      if (typeof data.federation_objects_count === "number" && data.federation_objects_count > 0) {
        setCancelWarn(tr("nav.queue.modal_federation_warn", { count: data.federation_objects_count }));
      }
    } else if (action === "cancel-staged") {
      renderCancelImpact([
        { label: tr("nav.queue.modal_impact_docs"), value: data.purged_count },
        { label: tr("nav.queue.modal_impact_files"), value: data.disk_files_count }
      ]);
      if (data.claimed_active || item.claimed_active) {
        var clientName = data.claimed_by || item.claimed_by || tr("nav.queue.unknown_client");
        setCancelWarn(tr("nav.queue.modal_staged_claim_warn", { client: clientName }));
        if (forceRow) forceRow.hidden = false;
      }
    } else if (action === "cancel-inbox" && typeof data.disk_files_count === "number" && data.disk_files_count > 0) {
      renderCancelImpact([{ label: tr("nav.queue.modal_impact_files"), value: data.disk_files_count }]);
    }
    syncCancelConfirmState();
  }

  function openQueueCancelModal(action, item) {
    if (!item) return;
    var dlg = ensureCancelDialog();
    cancelModal.action = action;
    cancelModal.item = item;
    cancelModal.dryRunData = null;
    cancelModal.dryRunFailed = false;
    cancelModal.submitting = false;
    cancelModal.wasQueueOpen = isQueueOpen();
    var mySeq = ++cancelModal.seq;

    var titleEl = document.getElementById("queue-cancel-title");
    var targetEl = document.getElementById("queue-cancel-target");
    var introEl = document.getElementById("queue-cancel-intro");
    var tombRow = document.getElementById("queue-cancel-tombstone-row");
    var tombChk = document.getElementById("queue-cancel-tombstone");
    var forceRow = document.getElementById("queue-cancel-force-row");
    var forceChk = document.getElementById("queue-cancel-force");
    var confirmBtn = document.getElementById("queue-cancel-confirm");

    if (targetEl) targetEl.textContent = item.title || tr("nav.queue.untitled");
    if (action === "purge-done") {
      if (titleEl) titleEl.textContent = tr("nav.queue.modal_purge_title");
      if (introEl) introEl.textContent = tr("nav.queue.modal_purge_desc");
    } else if (action === "cancel-staged") {
      if (titleEl) titleEl.textContent = tr("nav.queue.modal_cancel_staged_title");
      if (introEl) introEl.textContent = tr("nav.queue.modal_cancel_staged_desc");
    } else if (action === "cancel-running") {
      if (titleEl) titleEl.textContent = tr("nav.queue.modal_cancel_running_title");
      if (introEl) introEl.textContent = tr("nav.queue.modal_cancel_running_desc");
    } else {
      if (titleEl) titleEl.textContent = tr("nav.queue.modal_cancel_inbox_title");
      if (introEl) introEl.textContent = tr("nav.queue.modal_cancel_inbox_desc");
    }

    setCancelWarn("");
    setCancelInfo("");
    renderCancelImpact([]);
    setCancelMsg("", false);

    var purgeAllowed = queue.data.purge_allowed !== false;
    if (tombRow && tombChk) {
      if (action === "purge-done") {
        tombRow.hidden = false;
        tombChk.disabled = !purgeAllowed;
        tombChk.checked = purgeAllowed;
      } else {
        tombRow.hidden = !purgeAllowed;
        tombChk.disabled = !purgeAllowed;
        tombChk.checked = false;
      }
    }
    if (forceRow && forceChk) {
      forceChk.checked = false;
      var claimed = action === "cancel-staged" && Boolean(item.claimed_active);
      forceRow.hidden = !claimed;
      if (claimed) {
        setCancelWarn(tr("nav.queue.modal_staged_claim_warn", { client: item.claimed_by || tr("nav.queue.unknown_client") }));
      }
    }
    if (confirmBtn) {
      confirmBtn.className = action === "purge-done" ? "danger" : "pri";
    }
    syncCancelConfirmState();

    if (!dlg.open) dlg.showModal();

    if (action !== "cancel-running" && item.inbox_id != null) {
      setCancelMsg(tr("nav.queue.modal_impact_loading"), false);
      fetch(QUEUE_PATH + "/" + encodeURIComponent(String(item.inbox_id)) + "/cancel", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ dry_run: true, tombstone: Boolean(tombChk && tombChk.checked) })
      }).then(function (res) {
        return res.json().catch(function () { return null; }).then(function (data) {
          return { ok: res.ok, status: res.status, data: data };
        });
      }).then(function (result) {
        if (cancelModal.seq !== mySeq || !dlg.open) return;
        if (!result.ok) {
          cancelModal.dryRunFailed = result.status === 403 || result.status === 404;
          setCancelMsg(localizedCancelError(result.status, result.data), true);
          syncCancelConfirmState();
          return;
        }
        setCancelMsg("", false);
        applyDryRunResponse(result.data);
      }).catch(function () {
        if (cancelModal.seq !== mySeq || !dlg.open) return;
        setCancelMsg("", false);
      });
    }
  }

  function submitQueueCancelModal() {
    var dlg = document.getElementById("modal-queue-cancel");
    var tombChk = document.getElementById("queue-cancel-tombstone");
    var forceChk = document.getElementById("queue-cancel-force");
    var item = cancelModal.item;
    var action = cancelModal.action;
    if (!dlg || !item || cancelModal.submitting) return;

    cancelModal.submitting = true;
    setCancelMsg("", false);
    syncCancelConfirmState();

    var url;
    var payload;
    if (action === "cancel-running") {
      var jobId = item.job_id || item.id;
      url = QUEUE_PATH + "/jobs/" + encodeURIComponent(String(jobId)) + "/cancel";
      payload = { tombstone: Boolean(tombChk && tombChk.checked) };
    } else {
      url = QUEUE_PATH + "/" + encodeURIComponent(String(item.inbox_id)) + "/cancel";
      payload = { dry_run: false, tombstone: Boolean(tombChk && tombChk.checked), force: Boolean(forceChk && forceChk.checked) };
    }

    fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(payload)
    }).then(function (res) {
      return res.json().catch(function () { return null; }).then(function (data) {
        return { ok: res.ok, status: res.status, data: data };
      });
    }).then(function (result) {
      cancelModal.submitting = false;
      if (!result.ok) {
        if (result.status === 409 && result.data && result.data.error === "staged_claim_active") {
          var forceRow = document.getElementById("queue-cancel-force-row");
          if (forceRow) forceRow.hidden = false;
          setCancelWarn(tr("nav.queue.modal_staged_claim_warn", { client: result.data.claimed_by || tr("nav.queue.unknown_client") }));
        }
        setCancelMsg(localizedCancelError(result.status, result.data), true);
        syncCancelConfirmState();
        return;
      }
      if (dlg.open) dlg.close();
      if (result.data && result.data.action === "cancel_queued") announceQueue(tr("nav.queue.announced_cancel_queued"));
      else if (action === "cancel-running") announceQueue(tr("nav.queue.announced_cancel_requested"));
      else if (action === "purge-done") announceQueue(tr("nav.queue.announced_purged"));
      else announceQueue(tr("nav.queue.announced_cancelled"));
      loadIngestQueue();
    }).catch(function () {
      cancelModal.submitting = false;
      setCancelMsg(tr("nav.queue.err_cancel_failed", { error: "network" }), true);
      syncCancelConfirmState();
    });
  }

  function renderQueueChrome() {
    var btn = document.getElementById("queuebtn");
    var badge = document.getElementById("queue-count");
    var dot = document.getElementById("queue-live-dot");
    if (!btn || !badge || !dot) return;
    var counts = queueCounts();
    var maxSlots = typeof queue.data.max_slots === "number" && queue.data.max_slots > 0 ? queue.data.max_slots : 4;
    var busySlots = typeof queue.data.busy_slots === "number" ? queue.data.busy_slots : counts.running;
    var activeTotal = counts.running + counts.queued + counts.staged;
    badge.textContent = activeTotal > 99 ? "99+" : String(activeTotal);
    badge.hidden = activeTotal === 0;
    badge.classList.toggle("is-saturated", busySlots >= maxSlots && maxSlots > 0);
    dot.hidden = counts.running === 0;
    dot.classList.toggle("is-pulsing", counts.running > 0);
    var label = (counts.running + counts.queued) > 0
      ? tr("nav.queue.open_active", { running: counts.running, queued: counts.queued })
      : tr("nav.queue.open");
    btn.title = label;
    btn.setAttribute("aria-label", label);
  }

  function renderQueueHeader() {
    var pop = document.getElementById("pop-queue");
    var meter = document.getElementById("queue-slot-meter");
    var slotLabel = document.getElementById("queue-slot-label");
    var toggleBtn = document.getElementById("queue-view-toggle");
    if (!pop) return;
    pop.setAttribute("data-queue-view", queue.viewMode);

    var counts = queueCounts();
    var maxSlots = typeof queue.data.max_slots === "number" && queue.data.max_slots > 0 ? queue.data.max_slots : 4;
    var busySlots = typeof queue.data.busy_slots === "number" ? queue.data.busy_slots : counts.running;
    if (meter && slotLabel) {
      slotLabel.textContent = tr("nav.queue.slots_label", { busy: busySlots, max: maxSlots });
      var cellsWrap = meter.querySelector(".slot-cells");
      if (cellsWrap) {
        cellsWrap.textContent = "";
        for (var s = 0; s < maxSlots; s++) {
          cellsWrap.appendChild(makeQueueEl("span", "slot-cell" + (s < busySlots ? " is-busy" : "")));
        }
      }
    }

    if (toggleBtn) {
      var isCompact = queue.viewMode === "compact";
      toggleBtn.setAttribute("aria-pressed", isCompact ? "true" : "false");
      var tip = tr(isCompact ? "nav.queue.switch_ledger" : "nav.queue.switch_compact");
      toggleBtn.title = tip;
      toggleBtn.setAttribute("aria-label", tip);
      var ic = toggleBtn.querySelector("[data-icon]");
      if (ic) {
        ic.setAttribute("data-icon", isCompact ? "clipboard" : "list");
        hydrateQueueIcons(toggleBtn);
      }
    }
  }

  function renderQueueEmpty(scroller, filtered) {
    var emp = makeQueueEl("div", "q-empty");
    emp.appendChild(makeQueueIcon(queue.loadFailed ? "warning" : "check-circle"));
    if (queue.loadFailed) {
      emp.appendChild(makeQueueEl("strong", "", tr("nav.queue.load_failed")));
    } else if (filtered) {
      emp.appendChild(makeQueueEl("strong", "", tr("nav.queue.empty_filtered_title")));
      emp.appendChild(makeQueueEl("span", "", tr("nav.queue.empty_filtered_desc")));
    } else {
      emp.appendChild(makeQueueEl("strong", "", tr("nav.queue.empty_title")));
      emp.appendChild(makeQueueEl("span", "", tr("nav.queue.empty_desc")));
    }
    scroller.appendChild(emp);
  }

  function renderQueueLedger(counts, filterBar, scroller) {
    var items = Array.isArray(queue.data.items) ? queue.data.items : [];
    var tabsDef = [
      { id: "all", label: tr("nav.queue.tab_all"), cnt: items.length },
      { id: "active", label: tr("nav.queue.tab_active"), cnt: counts.running + counts.queued },
      { id: "staged", label: tr("nav.queue.tab_staged"), cnt: counts.staged },
      { id: "error", label: tr("nav.queue.tab_error"), cnt: counts.error, isError: counts.error > 0 },
      { id: "done", label: tr("nav.queue.tab_done"), cnt: counts.done }
    ];
    if (!tabsDef.some(function (t) { return t.id === queue.tab; })) queue.tab = "all";

    var tablist = makeQueueEl("div", "q-tablist");
    tablist.setAttribute("focusgroup", "tablist nomemory");
    tablist.setAttribute("role", "tablist");
    tablist.setAttribute("aria-label", tr("nav.queue.filter_label"));

    var tabButtons = [];
    tabsDef.forEach(function (t) {
      var selected = queue.tab === t.id;
      var btn = makeQueueEl("button", "q-tab");
      btn.type = "button";
      btn.setAttribute("role", "tab");
      btn.setAttribute("data-filter-id", t.id);
      btn.setAttribute("aria-selected", selected ? "true" : "false");
      btn.tabIndex = selected ? 0 : -1;
      if (selected) btn.setAttribute("focusgroupstart", "");
      btn.appendChild(document.createTextNode(t.label));
      btn.appendChild(makeQueueEl("span", "q-tab-cnt" + (t.isError ? " is-error" : ""), String(t.cnt)));
      btn.addEventListener("click", function () {
        queue.tab = t.id;
        renderQueuePopover(false);
        var nextBtn = filterBar.querySelector('.q-tab[aria-selected="true"]');
        if (nextBtn) nextBtn.focus();
      });
      tablist.appendChild(btn);
      tabButtons.push(btn);
    });

    tablist.addEventListener("keydown", function (ev) {
      var idx = tabButtons.indexOf(document.activeElement);
      if (idx === -1) return;
      var next = -1;
      if (ev.key === "ArrowRight") next = (idx + 1) % tabButtons.length;
      else if (ev.key === "ArrowLeft") next = (idx - 1 + tabButtons.length) % tabButtons.length;
      else if (ev.key === "Home") next = 0;
      else if (ev.key === "End") next = tabButtons.length - 1;
      if (next !== -1) {
        ev.preventDefault();
        tabButtons[next].click();
      }
    });
    filterBar.appendChild(tablist);

    var listItems = queueFilteredItems();
    if (!listItems.length) {
      renderQueueEmpty(scroller, items.length > 0);
      return;
    }

    var groups = [
      { key: "running", title: tr("nav.queue.sec_running"), items: listItems.filter(function (i) { return i.status === "running"; }) },
      { key: "queued", title: tr("nav.queue.sec_queued"), items: listItems.filter(function (i) { return i.status === "queued"; }) },
      { key: "staged", title: tr("nav.queue.sec_staged"), items: listItems.filter(function (i) { return i.status === "staged"; }) },
      { key: "error", title: tr("nav.queue.sec_error"), items: listItems.filter(function (i) { return i.status === "error"; }) },
      { key: "done", title: tr("nav.queue.sec_done"), items: listItems.filter(function (i) { return i.status === "done"; }) }
    ];

    groups.forEach(function (g) {
      if (!g.items.length) return;
      var secHead = makeQueueEl("div", "q-sec-label");
      secHead.appendChild(makeQueueEl("span", "", g.title));
      secHead.appendChild(makeQueueEl("span", "tnum", tr("nav.queue.count_items", { count: g.items.length })));
      scroller.appendChild(secHead);

      var ol = makeQueueEl("ol", "q-list");
      ol.setAttribute("role", "list");
      g.items.forEach(function (item) {
        var st = String(item.status || "queued");
        var titleText = item.title || tr("nav.queue.untitled");
        var li = makeQueueEl("li", "qa-row st-" + st);
        var lead = makeQueueEl("div", "qa-lead");
        var ordText = st === "queued"
          ? ("#" + (item.order != null ? item.order : 1))
          : tr("nav.queue.pill_" + (st === "running" || st === "staged" || st === "error" || st === "done" ? st : "done"));
        lead.appendChild(makeQueueEl("span", "qa-order-pill mono", ordText));
        lead.appendChild(makeQueueIcon(queueStatusIcon(st)));
        li.appendChild(lead);

        var body = makeQueueEl("div", "qa-body");
        var top = makeQueueEl("div", "qa-top");
        top.appendChild(makeQueueEl("div", "qa-title", titleText));
        var ts = item.updated_at || item.created_at;
        if (typeof ts === "number" && ts > 0) {
          top.appendChild(makeQueueEl("span", "qa-time tnum", notifyTimeText(ts)));
        }
        body.appendChild(top);

        var meta = makeQueueEl("div", "qa-meta");
        var src = queueSourceMeta(item.source);
        meta.appendChild(makeQueueChip(src.label, "", src.icon));
        if (item.kb_label || item.kb_id) {
          meta.appendChild(makeQueueChip(String(item.kb_label || item.kb_id), ""));
        }
        var isClient = item.mode === "client";
        meta.appendChild(makeQueueChip(tr(isClient ? "nav.queue.mode_client" : "nav.queue.mode_provider"), isClient ? "is-staged" : ""));
        if (st === "running" && item.cancel_requested) {
          meta.appendChild(makeQueueChip(tr("nav.queue.cancelling_badge"), "is-cancelling", "clock"));
        } else if (st === "queued" && item.cancel_queued) {
          meta.appendChild(makeQueueChip(tr("nav.queue.cancel_queued_badge"), "is-cancelling", "clock"));
        } else if (st === "staged" && item.claimed_active) {
          meta.appendChild(makeQueueChip(tr("nav.queue.staged_claimed_badge"), "is-cancelling", "bot"));
        }
        body.appendChild(meta);

        var stageIdx = queueStageIdx(item);
        if (st === "running") {
          var sbox = makeQueueEl("div", "qa-stage-box");
          var prog = document.createElement("progress");
          prog.className = "q-progress";
          prog.max = 5;
          prog.value = stageIdx + 1;
          prog.setAttribute("aria-label", tr("nav.queue.progress_aria", { title: titleText }));
          sbox.appendChild(prog);

          var steps = makeQueueEl("div", "qa-stage-steps");
          QUEUE_STAGES.forEach(function (stKey, idx) {
            var cls = "qa-step";
            if (idx < stageIdx) cls += " is-done";
            else if (idx === stageIdx) cls += " is-now";
            steps.appendChild(makeQueueEl("span", cls, queueStageLabel(stKey)));
          });
          sbox.appendChild(steps);
          if (item.msg) sbox.appendChild(makeQueueEl("div", "qa-msg", String(item.msg)));
          body.appendChild(sbox);
        } else if (st === "error") {
          if (item.msg) body.appendChild(makeQueueEl("div", "qa-err", String(item.msg)));
        } else if (st === "queued" && item.cancel_queued && !item.msg) {
          body.appendChild(makeQueueEl("div", "qa-msg", tr("nav.queue.status_cancel_queued")));
        } else if (item.msg) {
          body.appendChild(makeQueueEl("div", "qa-msg", String(item.msg)));
        }

        var acts = makeQueueEl("div", "qa-actions");
        appendQueueItemActions(acts, item, st);
        if (acts.children.length) body.appendChild(acts);

        li.appendChild(body);
        ol.appendChild(li);
      });
      scroller.appendChild(ol);
    });
  }

  function renderQueueCompact(counts, filterBar, scroller) {
    var items = Array.isArray(queue.data.items) ? queue.data.items : [];
    var chipsDef = [
      { id: "all", label: tr("nav.queue.tab_all"), n: items.length },
      { id: "running", label: tr("nav.queue.chip_running"), n: counts.running },
      { id: "queued", label: tr("nav.queue.chip_queued"), n: counts.queued },
      { id: "staged", label: tr("nav.queue.chip_staged"), n: counts.staged },
      { id: "error", label: tr("nav.queue.chip_error"), n: counts.error, isError: counts.error > 0 },
      { id: "done", label: tr("nav.queue.chip_done"), n: counts.done }
    ];
    if (!chipsDef.some(function (c) { return c.id === queue.tab; })) queue.tab = "all";

    var bar = makeQueueEl("div", "qc-chips");
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", tr("nav.queue.filter_label"));
    chipsDef.forEach(function (c) {
      var b = makeQueueEl("button", "qc-chip-btn tnum" + (c.isError ? " is-error" : ""));
      b.type = "button";
      b.setAttribute("data-filter-id", c.id);
      b.setAttribute("aria-pressed", queue.tab === c.id ? "true" : "false");
      b.textContent = c.label + " " + c.n;
      b.addEventListener("click", function () {
        queue.tab = c.id;
        renderQueuePopover(false);
      });
      bar.appendChild(b);
    });
    filterBar.appendChild(bar);

    var listItems = queueFilteredItems();
    if (!listItems.length) {
      renderQueueEmpty(scroller, items.length > 0);
      return;
    }

    var ol = makeQueueEl("ol", "q-list");
    ol.setAttribute("role", "list");
    listItems.forEach(function (item) {
      var st = String(item.status || "queued");
      var rowKey = String(item.id != null ? item.id : (item.inbox_id != null ? item.inbox_id : item.title));
      var expanded = queue.expandedCompactId === rowKey;
      var stageIdx = queueStageIdx(item);
      var titleText = item.title || tr("nav.queue.untitled");

      var li = makeQueueEl("li", "qc-row st-" + st);
      var rowBtn = makeQueueEl("button", "qc-row-btn");
      rowBtn.type = "button";
      rowBtn.setAttribute("data-row-key", rowKey);
      rowBtn.setAttribute("aria-expanded", expanded ? "true" : "false");

      var posTxt = st === "queued"
        ? ("#" + (item.order != null ? item.order : 1))
        : (st === "running" ? (stageIdx + 1) + "/5" : tr("nav.queue.pill_" + (st === "staged" || st === "error" || st === "done" ? st : "done")));
      rowBtn.appendChild(makeQueueEl("span", "qc-pos mono", posTxt));

      var main = makeQueueEl("div", "qc-main");
      if ((st === "running" && item.cancel_requested) || (st === "queued" && item.cancel_queued) || (st === "staged" && item.claimed_active)) {
        var titleRow = makeQueueEl("div", "qc-title-row");
        titleRow.appendChild(makeQueueEl("div", "qc-title", titleText));
        if (st === "running" && item.cancel_requested) {
          titleRow.appendChild(makeQueueChip(tr("nav.queue.cancelling_badge"), "is-cancelling", "clock"));
        } else if (st === "queued" && item.cancel_queued) {
          titleRow.appendChild(makeQueueChip(tr("nav.queue.cancel_queued_badge"), "is-cancelling", "clock"));
        } else {
          titleRow.appendChild(makeQueueChip(tr("nav.queue.staged_claimed_badge"), "is-cancelling", "bot"));
        }
        main.appendChild(titleRow);
      } else {
        main.appendChild(makeQueueEl("div", "qc-title", titleText));
      }
      var src = queueSourceMeta(item.source);
      var subParts = [queueStatusLabel(item), src.label];
      if (item.kb_label || item.kb_id) subParts.push(String(item.kb_label || item.kb_id));
      main.appendChild(makeQueueEl("div", "qc-sub", subParts.join(" · ")));
      rowBtn.appendChild(main);

      var right = makeQueueEl("div", "qc-right tnum");
      var ts = item.updated_at || item.created_at;
      right.appendChild(makeQueueEl("span", "", typeof ts === "number" && ts > 0 ? notifyTimeText(ts) : ""));
      if (st === "running") {
        var prog = document.createElement("progress");
        prog.className = "q-progress";
        prog.max = 5;
        prog.value = stageIdx + 1;
        prog.setAttribute("aria-hidden", "true");
        right.appendChild(prog);
      } else {
        right.appendChild(makeQueueEl("span", "", tr(expanded ? "nav.queue.detail_collapse" : "nav.queue.detail_expand")));
      }
      rowBtn.appendChild(right);

      rowBtn.addEventListener("click", function () {
        queue.expandedCompactId = expanded ? null : rowKey;
        renderQueuePopover(true);
      });
      li.appendChild(rowBtn);

      if (expanded) {
        var det = makeQueueEl("div", "qc-detail");
        if (item.msg) det.appendChild(makeQueueEl("div", st === "error" ? "qa-err" : "qa-msg", String(item.msg)));
        else if (st === "queued" && item.cancel_queued) det.appendChild(makeQueueEl("div", "qa-msg", tr("nav.queue.status_cancel_queued")));
        var dm = makeQueueEl("div", "qc-detail-meta mono");
        dm.appendChild(makeQueueEl("span", "", tr("nav.queue.detail_id", { id: rowKey })));
        var modeText = tr(item.mode === "client" ? "nav.queue.mode_client_long" : "nav.queue.mode_provider_long");
        dm.appendChild(makeQueueEl("span", "", tr("nav.queue.detail_mode", { mode: modeText })));
        det.appendChild(dm);

        var dActs = makeQueueEl("div", "qc-detail-actions");
        appendQueueItemActions(dActs, item, st);
        if (dActs.children.length) det.appendChild(dActs);
        li.appendChild(det);
      }

      ol.appendChild(li);
    });
    scroller.appendChild(ol);
  }

  function renderQueuePopover(preserveScroll) {
    var pop = document.getElementById("pop-queue");
    var filterBar = document.getElementById("queue-filter-bar");
    var scroller = document.getElementById("queue-scroll");
    var summary = document.getElementById("queue-foot-summary");
    if (!pop || !filterBar || !scroller) return;

    var activeEl = document.activeElement;
    var focusedFilterId = activeEl && filterBar.contains(activeEl) ? activeEl.getAttribute("data-filter-id") : null;
    var focusedRowKey = activeEl && scroller.contains(activeEl) ? activeEl.getAttribute("data-row-key") : null;
    var prevScroll = preserveScroll ? scroller.scrollTop : 0;
    var counts = queueCounts();
    renderQueueHeader();

    filterBar.textContent = "";
    scroller.textContent = "";
    scroller.removeAttribute("data-io-bound");

    if (queue.viewMode === "compact") renderQueueCompact(counts, filterBar, scroller);
    else renderQueueLedger(counts, filterBar, scroller);

    if (summary) {
      summary.textContent = tr("nav.queue.foot_summary", { count: counts.running + counts.queued + counts.staged });
    }
    hydrateQueueIcons(pop);
    if (preserveScroll) scroller.scrollTop = prevScroll;
    if (focusedFilterId) {
      var targetFilter = Array.prototype.find.call(filterBar.querySelectorAll("[data-filter-id]"), function (el) {
        return el.getAttribute("data-filter-id") === focusedFilterId;
      });
      if (targetFilter) targetFilter.focus({ preventScroll: true });
    } else if (focusedRowKey) {
      var targetRow = Array.prototype.find.call(scroller.querySelectorAll("[data-row-key]"), function (el) {
        return el.getAttribute("data-row-key") === focusedRowKey;
      });
      if (targetRow) targetRow.focus({ preventScroll: true });
    }
    requestAnimationFrame(function () {
      if (isQueueOpen()) positionQueueFallback();
      attachQueueScrollFallback();
    });
  }

  function scheduleQueuePoll() {
    if (queue.timer) { clearTimeout(queue.timer); queue.timer = null; }
    if (!queue.started || queue.stopped) return;
    var counts = queueCounts();
    var delay = (isQueueOpen() || counts.running > 0 || counts.queued > 0) ? QUEUE_ACTIVE_POLL_MS : QUEUE_IDLE_POLL_MS;
    queue.timer = window.setTimeout(function () {
      if (!document.hidden) loadIngestQueue();
      else scheduleQueuePoll();
    }, delay);
  }

  function stopIngestQueue() {
    queue.stopped = true;
    queue.pendingReload = false;
    if (queue.timer) { clearTimeout(queue.timer); queue.timer = null; }
    var btn = document.getElementById("queuebtn");
    if (btn) btn.hidden = true;
    var pop = document.getElementById("pop-queue");
    if (pop && isQueueOpen()) {
      try { pop.hidePopover(); } catch (_) {}
    }
  }

  function loadIngestQueue() {
    if (queue.stopped) return Promise.resolve();
    if (queue.busy) {
      queue.pendingReload = true;
      return queue.inflight || Promise.resolve();
    }
    queue.busy = true;
    queue.inflight = fetch(QUEUE_PATH, {
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" }
    }).then(function (res) {
      if (res.status === 401 || res.status === 403 || res.status === 404) {
        stopIngestQueue();
        return null;
      }
      if (!res.ok) throw new Error("http_" + res.status);
      return res.json();
    }).then(function (state) {
      if (!state) return;
      var btn = document.getElementById("queuebtn");
      if (btn) btn.hidden = false;
      queue.loadFailed = false;
      queue.data = {
        max_slots: typeof state.max_slots === "number" && state.max_slots > 0 ? state.max_slots : 4,
        busy_slots: typeof state.busy_slots === "number" ? state.busy_slots : 0,
        purge_allowed: state.purge_allowed !== false,
        counts: state.counts && typeof state.counts === "object" ? state.counts : {},
        items: Array.isArray(state.items) ? state.items : []
      };
      renderQueueChrome();
      renderQueuePopover(true);
    }).catch(function () {
      queue.loadFailed = true;
      renderQueuePopover(true);
    }).then(function () {
      queue.busy = false;
      queue.inflight = null;
      if (queue.pendingReload && !queue.stopped) {
        queue.pendingReload = false;
        return loadIngestQueue();
      }
      scheduleQueuePoll();
    });
    return queue.inflight;
  }

  function startIngestQueue() {
    var btn = document.getElementById("queuebtn");
    var pop = document.getElementById("pop-queue");
    if (!btn || !pop || queue.started) return;
    queue.started = true;
    queue.stopped = false;

    pop.addEventListener("beforetoggle", function (ev) {
      if (ev.newState === "open") {
        pop.removeAttribute("data-closing");
        requestAnimationFrame(positionQueueFallback);
      }
    });

    pop.addEventListener("toggle", function (ev) {
      var opened = ev.newState === "open";
      btn.setAttribute("aria-expanded", opened ? "true" : "false");
      if (opened) {
        closePopovers(pop);
        renderQueuePopover(false);
        positionQueueFallback();
        attachQueueScrollFallback();
        loadIngestQueue();
      } else {
        pop.removeAttribute("data-closing");
      }
      scheduleQueuePoll();
    });

    var viewToggle = document.getElementById("queue-view-toggle");
    if (viewToggle) {
      viewToggle.addEventListener("click", function () {
        queue.viewMode = queue.viewMode === "compact" ? "ledger" : "compact";
        saveQueueViewMode(queue.viewMode);
        var validTabs = queue.viewMode === "compact" ? ["all", "running", "queued", "staged", "error", "done"] : ["all", "active", "staged", "error", "done"];
        if (validTabs.indexOf(queue.tab) === -1) queue.tab = "all";
        renderQueuePopover(false);
        announceQueue(tr(queue.viewMode === "compact" ? "nav.queue.announced_compact" : "nav.queue.announced_ledger"));
      });
    }

    var closeBtn = document.getElementById("queue-close-btn");
    if (closeBtn) {
      closeBtn.addEventListener("click", function () {
        var returnFocus = document.activeElement && pop.contains(document.activeElement);
        closeTopLayer(pop);
        if (returnFocus && btn) btn.focus();
      });
    }

    window.addEventListener("resize", function () {
      if (isQueueOpen()) positionQueueFallback();
    });
    window.addEventListener("scroll", function () {
      if (isQueueOpen()) positionQueueFallback();
    }, { passive: true });
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden && queue.started && !queue.stopped) loadIngestQueue();
    });

    renderQueueHeader();
    loadIngestQueue();
  }

  function logoutShown() {
    var form = document.getElementById("menu-logout-form");
    return !!form && !form.hidden;
  }

  function boot() {
    syncThemeIcons(document.documentElement.getAttribute("data-theme") || "light");
    var themeBtn = document.getElementById("themebtn");
    if (themeBtn && themeBtn.dataset.appbarBound !== "1") {
      themeBtn.dataset.appbarBound = "1";
      themeBtn.addEventListener("click", function () { toggleTheme(); });
    }
    var paletteBtn = document.getElementById("palettetrigger");
    if (paletteBtn && paletteBtn.dataset.appbarBound !== "1") {
      paletteBtn.dataset.appbarBound = "1";
      paletteBtn.addEventListener("click", function () {
        if (window.ClaireCommandPalette && typeof window.ClaireCommandPalette.open === "function") {
          window.ClaireCommandPalette.open();
        }
      });
    }
    document.querySelectorAll(".appbar [commandfor][command='toggle-popover']").forEach(bindPopoverMenu);
    installClosedByFallback();
    // A page that shows the sign-out control has a signed-in person.
    if (logoutShown()) {
      startNotifications();
      startIngestQueue();
    }
  }

  window.ClaireAppBar = {
    applyTheme: applyTheme,
    toggleTheme: toggleTheme,
    closePopovers: closePopovers,
    initMenus: boot,
    setConsoleLink: function (opts) {
      var el = document.getElementById("consolelinkbtn");
      if (!el || !opts) return;
      if (typeof opts.visible === "boolean") el.hidden = !opts.visible;
      if (opts.href) el.setAttribute("href", opts.href);
      if (opts.label) {
        el.title = opts.label;
        el.setAttribute("aria-label", opts.label);
        var lbl = el.querySelector(".btn-label");
        if (lbl) lbl.textContent = opts.label;
      }
      if (opts.icon) {
        var ic = el.querySelector("[data-icon]");
        if (ic) ic.setAttribute("data-icon", opts.icon);
      }
    },
    setLogoutVisible: function (visible) {
      var form = document.getElementById("menu-logout-form");
      if (form) form.hidden = !visible;
      if (visible) {
        startNotifications();
        startIngestQueue();
      }
    },
    refreshNotifications: function () {
      return notify.started ? loadNotifications() : Promise.resolve();
    },
    refreshIngestQueue: function () {
      if (queue.stopped) return Promise.resolve();
      if (queue.started) return loadIngestQueue();
      if (logoutShown()) {
        startIngestQueue();
        return queue.inflight || Promise.resolve();
      }
      return Promise.resolve();
    },
    setModerationLink: function (opts) {
      var el = document.getElementById("moderationlinkbtn");
      if (!el || !opts) return;
      if (typeof opts.visible === "boolean") el.hidden = !opts.visible;
      if (opts.href) el.setAttribute("href", opts.href);
      if (opts.label) {
        el.title = opts.label;
        el.setAttribute("aria-label", opts.label);
        var lbl = el.querySelector(".btn-label");
        if (lbl) lbl.textContent = opts.label;
      }
      if (opts.icon) {
        var ic = el.querySelector("[data-icon]");
        if (ic) ic.setAttribute("data-icon", opts.icon);
      }
    },
    setModerationVisible: function (visible) {
      var el = document.getElementById("moderationlinkbtn");
      if (el) el.hidden = !visible;
    },
    setSyndicationVisible: function (visible) {
      var link = document.getElementById("syndlink");
      if (link) link.hidden = !visible;
      var menuLink = document.getElementById("menusyndlink");
      if (menuLink) menuLink.hidden = !visible;
    }
  };

  if (typeof window.toggleClaireTheme !== "function") {
    window.toggleClaireTheme = toggleTheme;
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
