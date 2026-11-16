(function () {
  "use strict";

  var handlers = null;

  function byId(id) { return document.getElementById(id); }

  function closeDialog(id) {
    var dlg = typeof id === "string" ? byId(id) : id;
    if (dlg && dlg.open) dlg.close();
  }

  function showMsg(id, text, isError) {
    var el = byId(id);
    if (!el) return;
    el.textContent = text || "";
    if (!text) el.removeAttribute("data-state");
    else el.setAttribute("data-state", isError ? "error" : "ok");
  }

  function configureThemeSelect(wrapId, selectId, helpId, config) {
    var wrap = byId(wrapId);
    var sel = byId(selectId);
    var help = byId(helpId);
    if (!wrap || !sel) return;
    if (!config || !config.visible || !Array.isArray(config.themes) || config.themes.length === 0) {
      wrap.hidden = true;
      sel.innerHTML = "";
      return;
    }
    wrap.hidden = false;
    if (help) help.textContent = config.helpText || "";
    sel.innerHTML = config.optionsHtml || config.themes.map(function (t) {
      var val = String(t.value);
      var selected = val === String(config.selectedValue) ? " selected" : "";
      var prefix = t.icon ? t.icon + " " : "";
      return '<option value="' + val.replace(/"/g, "&quot;") + '"' + selected + ">" + String(prefix + (t.label || val)).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") + "</option>";
    }).join("");
  }

  function openIngestDialog(opts) {
    var dlg = byId("modal-ingest");
    if (!dlg) return;
    opts = opts || {};
    showMsg("ingest-modal-msg", "", false);
    if (opts.themeConfig) {
      configureThemeSelect("ingest-theme-wrap", "ingtheme", "ingtheme-help", opts.themeConfig);
    }
    var focusEl = byId("ingfocus");
    var themeEl = byId("ingtheme");
    function syncFocusPlaceholder() {
      if (!focusEl || typeof opts.getFocusPlaceholder !== "function") return;
      focusEl.placeholder = opts.getFocusPlaceholder(themeEl ? themeEl.value : "");
    }
    if (themeEl) themeEl.onchange = syncFocusPlaceholder;
    syncFocusPlaceholder();
    if (!dlg.open) dlg.showModal();
    var ta = byId("ingin");
    if (ta) ta.focus();
  }

  function openReingestDialog(opts) {
    var dlg = byId("modal-reingest");
    if (!dlg) return;
    opts = opts || {};
    showMsg("reingest-modal-msg", "", false);
    if (opts.themeConfig) {
      configureThemeSelect("reingest-theme-wrap", "reingtheme", "reingtheme-help", opts.themeConfig);
    }
    var targetEl = byId("reingtarget");
    if (targetEl && typeof opts.target === "string") targetEl.value = opts.target;
    var subEl = byId("reingest-selected-doc");
    if (subEl) {
      if (opts.selectedDocLabel) {
        subEl.hidden = false;
        subEl.innerHTML = opts.selectedDocLabel;
      } else {
        subEl.hidden = true;
        subEl.textContent = "";
      }
    }
    if (!dlg.open) dlg.showModal();
    if (targetEl) targetEl.focus();
  }

  function openRegenDialog(opts) {
    var dlg = byId("modal-regen");
    if (!dlg) return;
    opts = opts || {};
    showMsg("regen-modal-msg", "", false);
    var resEl = byId("regenresult");
    if (resEl) resEl.innerHTML = "";
    if (opts.themeConfig) {
      configureThemeSelect("regen-theme-wrap", "regentheme", "regentheme-help", opts.themeConfig);
    }
    var targetEl = byId("regentarget");
    if (targetEl && typeof opts.target === "string") targetEl.value = opts.target;
    var subEl = byId("regen-selected-doc");
    if (subEl) {
      if (opts.selectedDocLabel) {
        subEl.hidden = false;
        subEl.innerHTML = opts.selectedDocLabel;
      } else {
        subEl.hidden = true;
        subEl.textContent = "";
      }
    }
    var comp = opts.component || "all";
    var radio = byId("regencomp-" + comp);
    if (radio) radio.checked = true;
    if (!dlg.open) dlg.showModal();
    if (targetEl) targetEl.focus();
  }

  function bindEvents() {
    document.addEventListener("click", function (ev) {
      var closeBtn = ev.target && ev.target.closest("[data-close-dialog]");
      if (closeBtn) {
        closeDialog(closeBtn.getAttribute("data-close-dialog"));
        return;
      }
      var cmdBtn = ev.target && ev.target.closest("[data-ingest-cmd]");
      if (cmdBtn) {
        var cmd = cmdBtn.getAttribute("data-ingest-cmd");
        if (window.ClaireAppBar) window.ClaireAppBar.closePopovers();
        if (!handlers) return;
        if (cmd === "ingest" && typeof handlers.openIngest === "function") handlers.openIngest();
        else if (cmd === "reingest" && typeof handlers.openReingest === "function") handlers.openReingest();
        else if (cmd === "regen" && typeof handlers.openRegen === "function") handlers.openRegen();
      }
    });

    var formIngest = byId("form-ingest");
    if (formIngest && formIngest.dataset.bound !== "1") {
      formIngest.dataset.bound = "1";
      formIngest.addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (handlers && typeof handlers.submitIngest === "function") handlers.submitIngest();
      });
    }

    var formReingest = byId("form-reingest");
    if (formReingest && formReingest.dataset.bound !== "1") {
      formReingest.dataset.bound = "1";
      formReingest.addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (handlers && typeof handlers.submitReingest === "function") handlers.submitReingest();
      });
    }

    var formRegen = byId("form-regen");
    if (formRegen && formRegen.dataset.bound !== "1") {
      formRegen.dataset.bound = "1";
      formRegen.addEventListener("submit", function (ev) {
        ev.preventDefault();
        if (handlers && typeof handlers.submitRegen === "function") handlers.submitRegen(false);
      });
    }

    var dryRunBtn = byId("btn-regen-dryrun");
    if (dryRunBtn && dryRunBtn.dataset.bound !== "1") {
      dryRunBtn.dataset.bound = "1";
      dryRunBtn.addEventListener("click", function () {
        if (handlers && typeof handlers.submitRegen === "function") handlers.submitRegen(true);
      });
    }
  }

  var bound = false;

  window.ClaireIngestDialogs = {
    init: function (cfg) {
      handlers = cfg || {};
      if (!bound) {
        bound = true;
        bindEvents();
      }
    },
    openIngestDialog: openIngestDialog,
    openReingestDialog: openReingestDialog,
    openRegenDialog: openRegenDialog,
    closeDialog: closeDialog,
    showMsg: showMsg
  };
})();
