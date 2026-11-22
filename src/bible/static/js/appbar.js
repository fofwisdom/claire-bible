(function () {
  "use strict";

  function syncThemeIcons(theme) {
    var nextIcon = theme === "dark" ? "sun" : "moon";
    var btn = document.getElementById("themebtn");
    if (btn) btn.setAttribute("data-icon", nextIcon);
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

  function positionMenuFallback(trigger, menu) {
    if (!trigger || !menu || supportsNativeAnchorPos()) return;
    var r = trigger.getBoundingClientRect();
    var mW = menu.offsetWidth || 220;
    var mH = menu.offsetHeight || 160;
    var top = r.bottom + 6;
    if (top + mH > window.innerHeight - 8 && r.top - 6 - mH >= 8) {
      top = r.top - 6 - mH;
    }
    var left = menu.classList.contains("app-popover-end") ? (r.right - mW) : r.left;
    left = Math.max(8, Math.min(left, window.innerWidth - mW - 8));
    menu.style.top = Math.round(top) + "px";
    menu.style.left = Math.round(left) + "px";
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
      row.href = typeof item.url === "string" && item.url.charAt(0) === "/" && item.url.charAt(1) !== "/" ? item.url : "/settings/notifications";
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
    if (logoutShown()) startNotifications();
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
      if (visible) startNotifications();
    },
    refreshNotifications: function () {
      return notify.started ? loadNotifications() : Promise.resolve();
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
