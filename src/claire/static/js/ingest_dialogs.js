(function () {
  "use strict";

  var handlers = null;
  var currentEffortSpec = null;
  var EFFORT_LABELS = { none: "None", minimal: "Minimal", low: "Low", medium: "Medium", high: "High", xhigh: "Very high", max: "Maximum" };

  function byId(id) { return document.getElementById(id); }

  function translate(key, vars, fallback) {
    if (handlers && typeof handlers.tr === "function") {
      return handlers.tr(key, vars);
    }
    if (window.ClaireI18n && typeof window.ClaireI18n.t === "function") {
      return window.ClaireI18n.t(key, vars);
    }
    return fallback || key;
  }

  function formatEffortLabel(val) {
    var key = "ws.ingest.effort_" + val;
    var out = translate(key, null, EFFORT_LABELS[val] || val);
    return out === key ? (EFFORT_LABELS[val] || val) : out;
  }

  function renderEffortGrid(grid, spec) {
    if (!grid) return;
    var prefix = grid.getAttribute("data-effort-prefix") || "ingeffort";
    var name = grid.getAttribute("data-effort-name") || "ingest-effort";
    var hintId = grid.getAttribute("data-effort-hint-id") || "";
    var checkedEl = grid.querySelector('input[type="radio"]:checked');
    var prevVal = checkedEl ? checkedEl.value : "";

    var options = (spec && Array.isArray(spec.options)) ? spec.options : [];
    var fixed = !!(spec && spec.fixed);
    var prov = (spec && spec.provider) ? String(spec.provider) : "";
    var model = (spec && spec.model) ? String(spec.model) : "";
    var tierMax = (spec && spec.tier_max_effort) ? String(spec.tier_max_effort) : "";
    var fixedEffort = (spec && spec.fixed_effort) ? String(spec.fixed_effort) : "";

    var choices = ['<div class="ingest-choice"><input id="' + prefix + '-default" type="radio" name="' + name + '" value=""/><label for="' + prefix + '-default" data-i18n="ws.ingest.effort_default">' + String(translate("ws.ingest.effort_default", null, "Default")).replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</label></div>"];
    if (!fixed) {
      for (var i = 0; i < options.length; i++) {
        var opt = String(options[i] || "").trim().toLowerCase();
        if (!opt) continue;
        var safeOpt = opt.replace(/[^a-z0-9_-]/g, "");
        var lbl = formatEffortLabel(safeOpt);
        choices.push('<div class="ingest-choice"><input id="' + prefix + "-" + safeOpt + '" type="radio" name="' + name + '" value="' + safeOpt + '"/><label for="' + prefix + "-" + safeOpt + '" data-i18n="ws.ingest.effort_' + safeOpt + '">' + String(lbl).replace(/&/g, "&amp;").replace(/</g, "&lt;") + "</label></div>");
      }
    }
    grid.innerHTML = choices.join("");

    var targetVal = (!fixed && prevVal && options.indexOf(prevVal) >= 0) ? prevVal : "";
    var radioToSelect = byId(prefix + "-" + (targetVal || "default"));
    if (radioToSelect) radioToSelect.checked = true;

    var hintEl = hintId ? byId(hintId) : null;
    if (hintEl) {
      if (!spec) {
        hintEl.setAttribute("data-i18n", "ws.ingest.effort_hint");
        hintEl.textContent = translate("ws.ingest.effort_hint", null, "");
      } else if (prov === "mock") {
        hintEl.removeAttribute("data-i18n");
        hintEl.textContent = translate("ws.ingest.effort_mock_hint", null, "Mock provider ignores reasoning options");
      } else if (fixed) {
        hintEl.removeAttribute("data-i18n");
        if (fixedEffort) {
          hintEl.textContent = translate("ws.ingest.effort_fixed_hint_val", { effort: formatEffortLabel(fixedEffort), provider: prov, model: model || "-" }, "Fixed at " + fixedEffort);
        } else {
          hintEl.textContent = translate("ws.ingest.effort_fixed_hint", { provider: prov || "unavailable", model: model || "-" }, "Fixed by model");
        }
      } else if (tierMax) {
        hintEl.removeAttribute("data-i18n");
        hintEl.textContent = translate("ws.ingest.effort_tier_hint", { provider: prov, model: model || "-", tier: formatEffortLabel(tierMax) }, prov + " (" + (model || "-") + ", max " + tierMax + ")");
      } else if (prov) {
        hintEl.removeAttribute("data-i18n");
        hintEl.textContent = translate("ws.ingest.effort_provider_hint", { provider: prov, model: model || "-" }, prov + " (" + (model || "-") + ")");
      }
    }
  }

  function renderEffortGrids(spec) {
    if (spec !== undefined) currentEffortSpec = spec || null;
    var grids = document.querySelectorAll(".ingest-choice-grid.effort");
    for (var i = 0; i < grids.length; i++) {
      renderEffortGrid(grids[i], currentEffortSpec);
    }
  }

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

  function openIngestDialog(opts) {
    var dlg = byId("modal-ingest");
    if (!dlg) return;
    opts = opts || {};
    showMsg("ingest-modal-msg", "", false);
    var noticesEl = byId("ingest-notices");
    if (noticesEl) noticesEl.innerHTML = "";
    renderEffortGrid(byId("ingest-effort-grid"), opts.effortSpec !== undefined ? opts.effortSpec : currentEffortSpec);
    if (!dlg.open) dlg.showModal();
    var ta = byId("ingin");
    if (ta) ta.focus();
  }

  function openReingestDialog(opts) {
    var dlg = byId("modal-reingest");
    if (!dlg) return;
    opts = opts || {};
    showMsg("reingest-modal-msg", "", false);
    renderEffortGrid(byId("reingest-effort-grid"), opts.effortSpec !== undefined ? opts.effortSpec : currentEffortSpec);
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
    renderEffortGrid(byId("regen-effort-grid"), opts.effortSpec !== undefined ? opts.effortSpec : currentEffortSpec);
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

    // Design Section 8.7: the standing message of the ingest form has the link "make a shared knowledge base". A page without the dialogs of the shared knowledge bases opens the console page that has the same action.
    var createKbBtn = byId("btn-ingest-create-kb");
    if (createKbBtn && createKbBtn.dataset.bound !== "1") {
      createKbBtn.dataset.bound = "1";
      createKbBtn.addEventListener("click", function () {
        closeDialog("modal-ingest");
        if (window.ClaireKbDialogs && typeof window.ClaireKbDialogs.openCreateKbDialog === "function") {
          window.ClaireKbDialogs.openCreateKbDialog({ defaultType: "fictional" });
        } else {
          window.location.assign("/settings/kb");
        }
      });
    }

    document.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest("[data-campaign-contribute]") : null;
      if (btn) contributeToTheme(btn);
    });
  }

  function escHtml(v) {
    return String(v == null ? "" : v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  // The notices of an ingest result (design Section 8.7 rule 2): a document that looks like fiction shows with the action "Contribute to this theme". The form shows the standing message each time, thus the notice "fiction_standing_message" has no use here.
  function ingestNoticesHtml(result) {
    var notices = (result && Array.isArray(result.notices)) ? result.notices : [];
    var h = "";
    for (var i = 0; i < notices.length; i++) {
      var n = notices[i] || {};
      if (n.code !== "fiction_like_document" || !n.document || !n.theme) continue;
      h += '<div class="claire-note fic campaign-notice" role="note"><span>' + escHtml(translate("campaign.fiction_like", { theme: n.theme_label || n.theme }, n.message || "")) + "</span>" +
        '<button type="button" class="claire-btn claire-btn-sm pri" data-campaign-contribute="' + escHtml(n.document) + '" data-campaign-theme="' + escHtml(n.theme) + '">' + escHtml(translate("campaign.contribute_action", null, "Contribute to this theme")) + "</button>" +
        '<span class="campaign-notice-msg" role="status" aria-live="polite"></span></div>';
    }
    return h;
  }

  // The text of a failed request: the catalog text of the error code (ClaireUi.errorText of shared_ui.js, which each page with this script loads first).
  function errorText(code) {
    if (window.ClaireUi && typeof window.ClaireUi.errorText === "function") return window.ClaireUi.errorText({ error: code });
    return translate("campaign.contribute_failed", { error: code }, "");
  }

  // POST /submissions with the document and the theme of the notice (design Section 9.2).
  function contributeToTheme(btn) {
    var doc = btn.getAttribute("data-campaign-contribute");
    var theme = btn.getAttribute("data-campaign-theme");
    var msg = btn.parentNode ? btn.parentNode.querySelector(".campaign-notice-msg") : null;
    btn.disabled = true;
    fetch("/submissions", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Accept": "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ documents: [doc], theme: theme })
    }).then(function (resp) {
      return resp.json().catch(function () { return null; }).then(function (data) {
        if (!resp.ok) {
          btn.disabled = false;
          if (msg) msg.textContent = errorText((data && (data.error || data.detail)) || ("HTTP " + resp.status));
          return;
        }
        var items = (data && data.submission && data.submission.items) || [];
        var targets = (items[0] && items[0].targets) || [];
        var sent = targets.filter(function (t) { return t.state !== "skipped"; }).length;
        btn.hidden = true;
        if (msg) msg.textContent = sent ? translate("campaign.contribute_done", { count: sent }, "") : translate("campaign.contribute_none", null, "");
      });
    }).catch(function () {
      btn.disabled = false;
      if (msg) msg.textContent = errorText("network_error");
    });
  }

  var bound = false;

  window.ClaireIngestDialogs = {
    init: function (cfg) {
      handlers = cfg || {};
      if (!bound) {
        bound = true;
        bindEvents();
      }
      if (handlers.effortSpec !== undefined) {
        renderEffortGrids(handlers.effortSpec);
      }
    },
    setEffortSpec: function (spec) {
      renderEffortGrids(spec);
    },
    getEffortSpec: function () {
      return currentEffortSpec;
    },
    openIngestDialog: openIngestDialog,
    openReingestDialog: openReingestDialog,
    openRegenDialog: openRegenDialog,
    closeDialog: closeDialog,
    showMsg: showMsg,
    ingestNoticesHtml: ingestNoticesHtml
  };
})();
