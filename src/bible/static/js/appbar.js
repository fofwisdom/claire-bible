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
