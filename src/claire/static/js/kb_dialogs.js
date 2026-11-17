/* Shared modal workflows for shared knowledge bases and themes (Prototype Item 5). */
(function (root) {
  "use strict";

  function ui() { return root.ClaireUi; }
  function t(key, params, fallback) { return ui().t(key, params, fallback); }
  function esc(v) { return ui().esc(v); }

  function openAutoRuleDryRunDialog(opts) {
    var payload = (opts && opts.payload) || {};
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML = '<p class="claire-hint">' + esc(t("kb.dlg.dry_run_loading", null, "Computing eligible documents and shared KB targets...")) + "</p>";

    return ui().openDialog({
      title: t("kb.dlg.dry_run_title", null, "Auto-contribution dry run"),
      icon: "bolt",
      bodyEl: box,
      submitLabel: null,
      cancelLabel: t("common.action.close", null, "Close"),
      onOpen: function () {
        ui().request("/api/auto-rule/dry-run", { method: "POST", body: payload }).then(function (res) {
          if (!res.ok || !res.data) {
            box.innerHTML = '<div class="claire-note warn">' + esc(res.unavailable ? t("kb.dlg.backend_pending", null, "Shared KB backend endpoint is not active on this server yet.") : (res.error || "Failed")) + "</div>";
            return;
          }
          var targets = res.data.targets || [];
          var h = '<div class="claire-note ok"><span>' + esc(t("kb.dlg.dry_run_summary", { count: targets.length }, targets.length + " target shared KB(s) matched.")) + "</span></div>";
          h += ui().renderTable({
            caption: t("kb.dlg.dry_run_title", null, "Auto-contribution dry run"),
            columns: [
              { label: t("kb.dlg.col_kb", null, "Shared KB"), render: function (r) { return '<a href="/kb/' + esc(r.knowledge_base) + '/">' + esc(r.label || ("#" + r.knowledge_base)) + "</a>"; } },
              { label: t("kb.dlg.col_mode", null, "Match mode"), render: function (r) { return '<span class="claire-tag acc">' + esc(r.mode || "theme") + "</span>"; } },
              { label: t("kb.dlg.col_docs", null, "Documents"), render: function (r) { return esc(Array.isArray(r.documents) ? r.documents.length : (r.documents || 0)); } }
            ],
            rows: targets
          });
          box.innerHTML = h;
          ui().enhance(box);
        });
      }
    });
  }

  function openNoThemePicker(opts) {
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML = '<p class="claire-hint">' + esc(t("kb.dlg.no_theme_desc", null, "This document does not strongly match a server theme yet. Pick a target shared KB directly or start a new one.")) + "</p>" +
      '<div id="claire-no-theme-list"><p class="claire-hint">' + esc(t("kb.ui.loading", null, "Loading...")) + "</p></div>";

    var createBtn = document.createElement("button");
    createBtn.type = "button";
    createBtn.className = "claire-btn sec";
    createBtn.textContent = t("kb.dlg.create_btn", null, "Create Shared KB");

    return ui().openDialog({
      title: t("kb.dlg.no_theme_title", null, "Pick a target Shared KB"),
      icon: "folder",
      bodyEl: box,
      extraFooterButtons: [createBtn],
      submitLabel: null,
      onOpen: function (dlg) {
        createBtn.addEventListener("click", function () {
          dlg.close("create");
          openCreateKbDialog({ onCreated: function () { if (opts && opts.onSubmitted) opts.onSubmitted(); } });
        });
        ui().request("/kb?page=true&limit=30").then(function (res) {
          var host = box.querySelector("#claire-no-theme-list");
          var items = (res.ok && res.data && (res.data.orderedItems || res.data.items)) || [];
          if (!items.length) {
            host.innerHTML = '<div class="claire-note warn">' + esc(res.unavailable ? t("kb.dlg.backend_pending", null, "Shared KB backend endpoint is not active on this server yet.") : t("kb.dlg.no_kbs_yet", null, "No shared knowledge bases available yet.")) + "</div>";
            return;
          }
          host.innerHTML = ui().renderTable({
            caption: t("kb.dlg.no_theme_title", null, "Pick a target Shared KB"),
            columns: [
              { label: t("kb.dlg.col_kb", null, "Shared KB"), render: function (r) { return "<strong>" + esc(r.label) + "</strong> <span class=\"claire-tag " + (r.knowledge_type === "fictional" ? "fic" : "acc") + "\">" + esc(r.knowledge_type || "real") + "</span>"; } },
              { label: t("kb.dlg.col_policy", null, "Policy"), render: function (r) { return esc(r.join_policy || "open"); } },
              { label: "", actions: true, render: function (r) { return '<button type="button" class="claire-btn pri claire-btn-sm" data-pick-kb="' + esc(r.number) + '">' + esc(t("kb.dlg.submit_here", null, "Submit")) + "</button>"; } }
            ],
            rows: items
          });
          host.addEventListener("click", function (ev) {
            var btn = ev.target && ev.target.closest ? ev.target.closest("[data-pick-kb]") : null;
            if (!btn) return;
            var kbNum = btn.getAttribute("data-pick-kb");
            btn.disabled = true;
            ui().request("/kb/" + encodeURIComponent(kbNum) + "/contributions", {
              method: "POST",
              body: { document: opts.documentId }
            }).then(function (sub) {
              if (sub.ok) {
                ui().toast(t("kb.dlg.contrib_done", null, "Contribution submitted."), "ok");
                dlg.close("submitted");
                if (opts && opts.onSubmitted) opts.onSubmitted(sub.data);
              } else {
                btn.disabled = false;
                ui().toast(sub.error || "Failed", "danger");
              }
            });
          });
        });
      }
    });
  }

  function openContribPreview(opts) {
    var docId = opts && opts.documentId;
    if (!docId) return Promise.resolve("");
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML =
      '<div class="claire-srow"><div class="claire-srow-info"><strong>' + esc((opts && opts.documentTitle) || docId) + '</strong><span>' + esc(t("kb.dlg.contrib_preview_desc", null, "Submit this extracted document to matching shared knowledge bases.")) + "</span></div></div>" +
      '<div id="claire-contrib-targets"></div>';

    var pickDirectBtn = document.createElement("button");
    pickDirectBtn.type = "button";
    pickDirectBtn.className = "claire-btn sec";
    pickDirectBtn.textContent = t("kb.dlg.pick_kb_direct", null, "Choose Shared KB directly");

    return ui().openDialog({
      title: t("kb.dlg.contrib_title", null, "Contribute document"),
      icon: "merge",
      bodyEl: box,
      extraFooterButtons: [pickDirectBtn],
      submitLabel: t("kb.dlg.contrib_submit_theme", null, "Submit by theme"),
      onOpen: function (dlg) {
        pickDirectBtn.addEventListener("click", function () {
          dlg.close("direct");
          openNoThemePicker(opts);
        });
        if (opts && opts.themeLabel) {
          box.querySelector("#claire-contrib-targets").innerHTML = '<div class="claire-note ok"><span>' + esc(t("kb.dlg.target_theme_label", { label: opts.themeLabel }, "Target theme: " + opts.themeLabel)) + "</span></div>";
        }
      },
      onSubmit: function (dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        var payload = { documents: [docId] };
        if (opts && opts.themeId) payload.theme = String(opts.themeId);
        return ui().request("/submissions", { method: "POST", body: payload }).then(function (res) {
          if (!res.ok) {
            if (submitBtn) submitBtn.disabled = false;
            var msg = res.unavailable ? t("kb.dlg.backend_pending", null, "Shared KB backend endpoint is not active on this server yet.") : (res.error || "Failed");
            box.querySelector("#claire-contrib-targets").innerHTML = '<div class="claire-note warn">' + esc(msg) + "</div>";
            return false;
          }
          var items = (res.data && res.data.submission && res.data.submission.items) || [];
          var first = items[0] || {};
          if (first.code === "no_theme") {
            dlg.close("no_theme");
            openNoThemePicker(opts);
            return false;
          }
          ui().toast(t("kb.dlg.contrib_done", null, "Contribution submitted."), "ok");
          if (opts && opts.onSubmitted) opts.onSubmitted(res.data);
          return true;
        });
      }
    });
  }

  function openCreateKbDialog(opts) {
    var box = document.createElement("form");
    box.className = "claire-panel-inner";
    box.noValidate = true;
    var defaultType = (opts && opts.defaultType) === "fictional" ? "fictional" : "real";
    box.innerHTML =
      '<div id="claire-similar-kbs" class="claire-panel-inner"><p class="claire-hint">' + esc(t("kb.dlg.loading_similar", null, "Checking existing local and federated shared KBs...")) + "</p></div>" +
      '<div class="claire-field">' +
      '  <label class="claire-field-label" for="newkb-label">' + esc(t("kb.dlg.field_name", null, "Shared KB name")) + "</label>" +
      '  <input id="newkb-label" name="label" class="claire-input" required maxlength="80" data-validate data-error-id="newkb-label-err" aria-invalid="false"/>' +
      '  <span id="newkb-label-err" class="claire-error" hidden></span>' +
      "</div>" +
      '<fieldset class="claire-field" style="border:0;padding:0;margin:0"><legend class="claire-field-label">' + esc(t("kb.dlg.field_type", null, "Knowledge type (required)")) + "</legend>" +
      '  <div class="claire-chips">' +
      '    <label class="claire-chip"><input type="radio" name="knowledge_type" value="real"' + (defaultType === "real" ? " checked" : "") + "/><span>" + esc(t("kb.dlg.type_real", null, "Real-world")) + "</span></label>" +
      '    <label class="claire-chip"><input type="radio" name="knowledge_type" value="fictional"' + (defaultType === "fictional" ? " checked" : "") + "/><span>" + esc(t("kb.dlg.type_fictional", null, "Fictional")) + "</span></label>" +
      "  </div>" +
      "</fieldset>" +
      '<div class="claire-field">' +
      '  <label class="claire-field-label" for="newkb-desc">' + esc(t("kb.dlg.field_desc", null, "Description")) + "</label>" +
      '  <textarea id="newkb-desc" name="description" class="claire-textarea" maxlength="400"></textarea>' +
      "</div>" +
      '<div class="claire-field">' +
      '  <label class="claire-field-label" for="newkb-seeds">' + esc(t("kb.dlg.field_seeds", null, "Seed entities (comma-separated, max 20)")) + "</label>" +
      '  <input id="newkb-seeds" name="seed_entities" class="claire-input" data-validate data-error-id="newkb-seeds-err" aria-invalid="false"/>' +
      '  <span id="newkb-seeds-err" class="claire-error" hidden></span>' +
      "</div>" +
      '<div class="claire-field">' +
      '  <label class="claire-field-label" for="newkb-model">' + esc(t("kb.dlg.field_embed_model", null, "Embedding model")) + "</label>" +
      '  <select id="newkb-model" name="embedding_model" class="claire-select"><option value="">' + esc(t("kb.dlg.embed_default", null, "Server configured default")) + "</option></select>" +
      "</div>" +
      '<div id="newkb-form-err" class="claire-note warn" hidden></div>';

    return ui().openDialog({
      title: t("kb.dlg.create_title", null, "Create Shared Knowledge Base"),
      icon: "plus",
      bodyEl: box,
      submitLabel: t("kb.dlg.create_btn", null, "Create Shared KB"),
      onOpen: function (_dlg, _b, submitBtn) {
        ui().request("/kb?page=true&limit=12").then(function (res) {
          var host = box.querySelector("#claire-similar-kbs");
          var items = (res.ok && res.data && (res.data.orderedItems || res.data.items)) || [];
          if (!items.length) {
            host.innerHTML = '<p class="claire-hint">' + esc(t("kb.dlg.similar_none", null, "No similar existing shared KBs found.")) + "</p>";
            return;
          }
          host.innerHTML = '<div class="claire-note"><span>' + esc(t("kb.dlg.similar_exists", null, "Existing shared KBs — consider joining or contributing before creating a duplicate:")) + "</span></div>" +
            '<div class="claire-tags">' + items.slice(0, 6).map(function (it) {
              return '<a class="claire-tag ' + (it.knowledge_type === "fictional" ? "fic" : "acc") + '" href="/kb/' + esc(it.number) + '/">' + esc(it.label) + "</a>";
            }).join("") + "</div>";
        });
        ui().request("/providers").then(function (res) {
          if (!res.ok || !res.data) return;
          var sel = box.querySelector("#newkb-model");
          var providers = res.data.providers || res.data.configured || [];
          if (Array.isArray(providers)) {
            providers.forEach(function (p) {
              var name = typeof p === "string" ? p : (p.embedding_model || p.model || p.id || "");
              if (!name) return;
              var opt = document.createElement("option");
              opt.value = name;
              opt.textContent = name;
              sel.appendChild(opt);
            });
          }
        });
        var seedsEl = box.querySelector("#newkb-seeds");
        seedsEl.addEventListener("input", function () {
          var seeds = seedsEl.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
          if (seeds.length > 20) {
            seedsEl.setCustomValidity(t("kb.dlg.err_seeds_max", null, "Up to 20 seed entities are allowed."));
          } else {
            seedsEl.setCustomValidity("");
          }
        });
        box.addEventListener("submit", function (ev) {
          ev.preventDefault();
          if (submitBtn) submitBtn.click();
        });
      },
      onSubmit: function (_dlg, _b, submitBtn) {
        var labelEl = box.querySelector("#newkb-label");
        var seedsEl = box.querySelector("#newkb-seeds");
        var seeds = seedsEl.value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
        seedsEl.setCustomValidity(seeds.length > 20 ? t("kb.dlg.err_seeds_max", null, "Up to 20 seed entities are allowed.") : "");
        if (!box.reportValidity()) return false;
        if (submitBtn) submitBtn.disabled = true;
        var ktype = (box.querySelector('input[name="knowledge_type"]:checked') || {}).value || "real";
        var payload = {
          label: labelEl.value.trim(),
          knowledge_type: ktype,
          description: box.querySelector("#newkb-desc").value.trim(),
          seed_entities: seeds
        };
        var modelVal = box.querySelector("#newkb-model").value;
        if (modelVal) payload.embedding_model = modelVal;
        return ui().request("/kb", { method: "POST", body: payload }).then(function (res) {
          var errBox = box.querySelector("#newkb-form-err");
          if (!res.ok || !res.data || !res.data.knowledge_base) {
            if (submitBtn) submitBtn.disabled = false;
            errBox.textContent = res.unavailable ? t("kb.dlg.backend_pending", null, "Shared KB backend endpoint is not active on this server yet.") : (res.error || "Failed");
            errBox.hidden = false;
            return false;
          }
          ui().toast(t("kb.dlg.create_done", null, "Shared knowledge base created."), "ok");
          if (opts && opts.onCreated) opts.onCreated(res.data.knowledge_base);
          return true;
        });
      }
    });
  }

  function openForkKbDialog(opts) {
    var kb = (opts && opts.kb) || {};
    return ui().openDialog({
      title: t("kb.dlg.fork_title", null, "Fork shared knowledge base"),
      icon: "fork",
      bodyHtml: '<p>' + esc(t("kb.dlg.fork_desc", { label: kb.label || ("#" + kb.number) }, 'Create your own copy forked from "' + (kb.label || kb.number) + '" preserving origin attribution.')) + "</p>" +
        '<div id="claire-fork-err" class="claire-note warn" hidden></div>',
      submitLabel: t("kb.dlg.fork_confirm", null, "Fork KB"),
      onSubmit: function (_dlg, body, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        return ui().request("/kb/" + encodeURIComponent(kb.number) + "/forks", { method: "POST", body: {} }).then(function (res) {
          if (!res.ok || !res.data || !res.data.knowledge_base) {
            if (submitBtn) submitBtn.disabled = false;
            var errEl = body.querySelector("#claire-fork-err");
            errEl.textContent = res.unavailable ? t("kb.dlg.backend_pending", null, "Shared KB backend endpoint is not active on this server yet.") : (res.error || "Failed");
            errEl.hidden = false;
            return false;
          }
          ui().toast(t("kb.dlg.fork_done", null, "Forked shared KB."), "ok");
          if (opts && opts.onForked) opts.onForked(res.data.knowledge_base);
          return true;
        });
      }
    });
  }

  function openRemoveContributionDialog(opts) {
    var box = document.createElement("form");
    box.className = "claire-panel-inner";
    box.innerHTML =
      '<div class="claire-note danger"><span>' + esc(t("kb.dlg.remove_warn", null, "Removing an approved contribution purges the document and records a tombstone preventing duplicate re-submission.")) + "</span></div>" +
      '<div class="claire-field"><label class="claire-field-label" for="rm-reason">' + esc(t("kb.dlg.remove_reason", null, "Reason code")) + "</label>" +
      '<select id="rm-reason" name="reason" class="claire-select" required>' +
      '<option value="off_topic">' + esc(t("kb.dlg.reason_off_topic", null, "Off topic")) + "</option>" +
      '<option value="duplicate">' + esc(t("kb.dlg.reason_duplicate", null, "Duplicate")) + "</option>" +
      '<option value="spam">' + esc(t("kb.dlg.reason_spam", null, "Spam / abuse")) + "</option>" +
      '<option value="policy">' + esc(t("kb.dlg.reason_policy", null, "Server or KB policy")) + "</option>" +
      '<option value="owner_request">' + esc(t("kb.dlg.reason_owner", null, "Contributor request")) + "</option>" +
      "</select></div>" +
      '<div id="rm-err" class="claire-note warn" hidden></div>';

    return ui().openDialog({
      title: t("kb.dlg.remove_title", null, "Remove contribution"),
      icon: "trash",
      danger: true,
      bodyEl: box,
      submitLabel: t("kb.dlg.remove_submit", null, "Remove and purge"),
      onSubmit: function (_dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        var reason = box.querySelector("#rm-reason").value;
        return ui().request("/kb/" + encodeURIComponent(opts.kbNumber) + "/contributions/" + encodeURIComponent(opts.contribNumber) + "/remove", {
          method: "POST",
          body: { reason: reason }
        }).then(function (res) {
          if (!res.ok) {
            if (submitBtn) submitBtn.disabled = false;
            var errEl = box.querySelector("#rm-err");
            errEl.textContent = res.error || "Failed";
            errEl.hidden = false;
            return false;
          }
          ui().toast(t("kb.dlg.remove_done", null, "Contribution removed."), "ok");
          if (opts && opts.onRemoved) opts.onRemoved(res.data);
          return true;
        });
      }
    });
  }

  function openMergeThemeDialog(opts) {
    var src = (opts && opts.sourceTheme) || {};
    var sameType = ((opts && opts.themes) || []).filter(function (th) {
      return String(th.number) !== String(src.number) && th.knowledge_type === src.knowledge_type;
    });
    var box = document.createElement("form");
    box.className = "claire-panel-inner";
    if (!sameType.length) {
      box.innerHTML = '<div class="claire-note warn">' + esc(t("kb.dlg.merge_none", null, "No other themes of the same knowledge type are available to merge into.")) + "</div>";
    } else {
      box.innerHTML =
        '<div class="claire-note warn">' + esc(t("kb.dlg.merge_warn", { label: src.label || src.number }, 'Merge "' + (src.label || src.number) + '" into another theme of the same type (' + (src.knowledge_type || "real") + ").")) + "</div>" +
        '<div class="claire-field"><label class="claire-field-label" for="merge-into">' + esc(t("kb.dlg.merge_target", null, "Target theme")) + "</label>" +
        '<select id="merge-into" class="claire-select" required>' +
        sameType.map(function (th) { return '<option value="' + esc(th.number) + '">' + esc(th.label) + " (#" + esc(th.number) + ")</option>"; }).join("") +
        "</select></div>" +
        '<div id="merge-err" class="claire-note warn" hidden></div>';
    }
    return ui().openDialog({
      title: t("kb.dlg.merge_title", null, "Merge theme"),
      icon: "merge",
      danger: true,
      bodyEl: box,
      submitLabel: sameType.length ? t("kb.dlg.merge_submit", null, "Merge themes") : null,
      onSubmit: function (_dlg, _b, submitBtn) {
        if (!sameType.length) return true;
        if (submitBtn) submitBtn.disabled = true;
        var into = box.querySelector("#merge-into").value;
        return ui().request("/api/admin/themes/" + encodeURIComponent(src.number) + "/merge", {
          method: "POST",
          body: { into: into }
        }).then(function (res) {
          if (!res.ok) {
            if (submitBtn) submitBtn.disabled = false;
            var errEl = box.querySelector("#merge-err");
            errEl.textContent = res.error || "Failed";
            errEl.hidden = false;
            return false;
          }
          ui().toast(t("kb.dlg.merge_done", null, "Themes merged."), "ok");
          if (opts && opts.onMerged) opts.onMerged(res.data);
          return true;
        });
      }
    });
  }

  function openDeleteKbDialog(opts) {
    var kbNumber = opts && opts.kbNumber;
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML = '<p class="claire-hint">' + esc(t("kb.dlg.delete_dry_loading", null, "Calculating purge impact...")) + "</p>";
    var canApply = false;

    return ui().openDialog({
      title: t("kb.dlg.delete_title", { label: (opts && opts.kbLabel) || ("#" + kbNumber) }, "Delete shared KB"),
      icon: "trash",
      danger: true,
      bodyEl: box,
      submitLabel: t("kb.dlg.delete_apply", null, "Confirm permanent deletion"),
      onOpen: function (_dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        ui().request("/kb/" + encodeURIComponent(kbNumber) + "/", { method: "DELETE" }).then(function (res) {
          if (!res.ok || !res.data || !res.data.would_delete) {
            box.innerHTML = '<div class="claire-note warn">' + esc(res.unavailable ? t("kb.dlg.backend_pending", null, "Shared KB backend endpoint is not active on this server yet.") : (res.error || "Failed")) + "</div>";
            return;
          }
          canApply = true;
          if (submitBtn) submitBtn.disabled = false;
          var wd = res.data.would_delete;
          box.innerHTML =
            '<div class="claire-note danger"><span>' + esc(t("kb.dlg.delete_impact", null, "Dry-run preview — this action permanently deletes all records below:")) + "</span></div>" +
            '<div class="claire-kpis">' +
            '<div class="claire-kpi"><strong>' + esc(wd.documents || 0) + "</strong><span>" + esc(t("kb.dlg.col_docs", null, "Documents")) + "</span></div>" +
            '<div class="claire-kpi"><strong>' + esc(wd.entities || 0) + "</strong><span>" + esc(t("kb.dlg.col_entities", null, "Entities")) + "</span></div>" +
            '<div class="claire-kpi"><strong>' + esc(wd.relations || 0) + "</strong><span>" + esc(t("kb.dlg.col_relations", null, "Relations")) + "</span></div>" +
            '<div class="claire-kpi"><strong>' + esc(wd.members || 0) + "</strong><span>" + esc(t("kb.dlg.col_members", null, "Members")) + "</span></div>" +
            '<div class="claire-kpi"><strong>' + esc(wd.contributions || 0) + "</strong><span>" + esc(t("kb.dlg.col_contributions", null, "Contributions")) + "</span></div>" +
            "</div>";
        });
      },
      onSubmit: function (_dlg, _b, submitBtn) {
        if (!canApply) return false;
        if (submitBtn) submitBtn.disabled = true;
        return ui().stepUp().then(function (verified) {
          if (!verified) {
            if (submitBtn) submitBtn.disabled = false;
            return false;
          }
          return ui().request("/kb/" + encodeURIComponent(kbNumber) + "/?apply=true", { method: "DELETE" }).then(function (res) {
            if (!res.ok) {
              if (submitBtn) submitBtn.disabled = false;
              ui().toast(res.error || "Failed", "danger");
              return false;
            }
            ui().toast(t("kb.dlg.delete_done", null, "Shared KB deleted."), "ok");
            if (opts && opts.onDeleted) opts.onDeleted(res.data);
            return true;
          });
        });
      }
    });
  }

  function openFictionNotice(opts) {
    return ui().openDialog({
      title: t("ws.kb.fiction_notice_title", null, "Fictional content notice"),
      icon: "warning",
      bodyHtml: '<div class="claire-note fic"><span>' + esc(t("ws.kb.fiction_notice_body", { title: (opts && opts.documentTitle) || "" }, "This document was extracted locally but appears fictional. Contributing it to a fictional shared theme keeps personal reality KBs clean.")) + "</span></div>",
      cancelLabel: t("ws.kb.fiction_later", null, "Later"),
      submitLabel: t("ws.kb.contribute_to_theme", null, "Contribute to this theme"),
      onSubmit: function (dlg) {
        dlg.close("contrib");
        openContribPreview({
          documentId: opts && opts.documentId,
          documentTitle: opts && opts.documentTitle,
          themeId: opts && opts.themeNumber,
          themeLabel: opts && opts.themeLabel
        });
        return false;
      }
    });
  }

  root.ClaireKbDialogs = {
    openAutoRuleDryRunDialog: openAutoRuleDryRunDialog,
    openNoThemePicker: openNoThemePicker,
    openContribPreview: openContribPreview,
    openCreateKbDialog: openCreateKbDialog,
    openForkKbDialog: openForkKbDialog,
    openRemoveContributionDialog: openRemoveContributionDialog,
    openMergeThemeDialog: openMergeThemeDialog,
    openDeleteKbDialog: openDeleteKbDialog,
    openFictionNotice: openFictionNotice
  };
})(window);
