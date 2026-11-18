/* Dialogs for shared knowledge bases and themes (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 8 to 10). Each request goes to a route of docs/implementation/API_HTTP_CONTRACT.md. */
(function (root) {
  "use strict";

  function ui() { return root.ClaireUi; }
  function t(key, params) { return ui().t(key, params); }
  function esc(v) { return ui().esc(v); }
  function errText(res) { return ui().errorText(res); }

  // Reason codes of a rejection and of a removal (contributions.py REASON_CODES).
  var REASON_CODES = ["off_subject", "wrong_knowledge_type", "duplicate", "low_quality", "spam", "other"];

  function typeTag(knowledgeType) {
    return '<span class="claire-tag ' + (knowledgeType === "fictional" ? "fic" : "acc") + '">' + esc(t(knowledgeType === "fictional" ? "kb.dlg.type_fictional" : "kb.dlg.type_real")) + "</span>";
  }

  function stateLabel(state) {
    var key = "kb.state." + state;
    var text = t(key);
    return text === key ? String(state || "") : text;
  }

  function seedList(text) {
    return String(text || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
  }

  // The documents of the personal knowledge base of the requester, as a list with check boxes. ``homeHref`` is the path of that knowledge base ("/" or "/@user/").
  // GET …/documents gives one page ("documents", "next_before", "total"): the parameter "q" is the search text (200 characters or less), and "before" is the cursor of the next page. A selected document stays in the list when the search text changes.
  function loadDocumentChoices(box, homeHref) {
    var base = (homeHref || "/") + "documents";
    var docs = [];
    var picked = {};
    var nextBefore = null;
    var total = null;
    var query = "";
    var serial = 0;
    var timer = null;
    box.innerHTML = '<div class="claire-field"><label class="claire-field-label" for="kb-doc-search">' + esc(t("kb.dlg.doc_search")) + '</label><input id="kb-doc-search" type="search" class="claire-input" maxlength="200" autocomplete="off"/></div>' +
      '<div data-doc-list><p class="claire-hint">' + esc(t("kb.ui.loading")) + "</p></div>";
    var searchInput = box.querySelector("#kb-doc-search");
    var listBox = box.querySelector("[data-doc-list]");

    function rowHtml(id, title, checked) {
      return '<label class="claire-srow"><input type="checkbox" name="doc" value="' + esc(id) + '"' + (checked ? " checked" : "") + '/><span class="claire-srow-info"><strong>' + esc(title || id) + "</strong></span></label>";
    }

    function draw(failure) {
      var inPage = {};
      docs.forEach(function (d) { inPage[String(d.id)] = true; });
      // The selected documents that the current search does not show come first.
      var rows = Object.keys(picked).filter(function (id) { return !inPage[id]; }).map(function (id) { return rowHtml(id, picked[id], true); })
        .concat(docs.map(function (d) { return rowHtml(d.id, d.title, Object.prototype.hasOwnProperty.call(picked, String(d.id))); }));
      var h = "";
      if (failure) h += '<div class="claire-note warn">' + esc(errText(failure)) + "</div>";
      if (rows.length) h += '<fieldset class="claire-field" style="border:0;padding:0;margin:0;max-height:260px;overflow:auto"><legend class="claire-field-label">' + esc(t("kb.dlg.pick_documents")) + "</legend>" + rows.join("") + "</fieldset>";
      else if (!failure) h += '<p class="claire-hint">' + esc(t(query ? "kb.dlg.doc_search_empty" : "kb.dlg.no_documents")) + "</p>";
      if (total != null && docs.length) h += '<p class="claire-hint" data-doc-count>' + esc(t("kb.dlg.doc_count", { shown: docs.length, total: total })) + "</p>";
      if (nextBefore) h += '<div class="claire-actions"><button type="button" class="claire-btn sec claire-btn-sm" data-doc-more>' + esc(t("kb.dlg.doc_more")) + "</button></div>";
      listBox.innerHTML = h;
    }

    function load(more) {
      serial += 1;
      var mine = serial;
      var params = [];
      if (query) params.push("q=" + encodeURIComponent(query));
      if (more && nextBefore) params.push("before=" + encodeURIComponent(nextBefore));
      return ui().request(base + (params.length ? "?" + params.join("&") : "")).then(function (res) {
        // An answer of an older request (the search text changed) is not shown.
        if (mine !== serial) return;
        if (!res.ok) {
          if (!more) { docs = []; nextBefore = null; total = null; }
          draw(res);
          return;
        }
        var page = (res.data && res.data.documents) || [];
        docs = more ? docs.concat(page) : page;
        nextBefore = (res.data && res.data.next_before) || null;
        total = res.data && typeof res.data.total === "number" ? res.data.total : null;
        draw(null);
      });
    }

    listBox.addEventListener("change", function (ev) {
      var input = ev.target;
      if (!input || input.name !== "doc") return;
      if (input.checked) {
        var label = input.closest("label");
        picked[input.value] = label ? label.textContent : input.value;
      } else {
        delete picked[input.value];
      }
    });
    listBox.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest("[data-doc-more]") : null;
      if (!btn) return;
      btn.disabled = true;
      load(true);
    });
    function search() {
      if (timer) clearTimeout(timer);
      timer = null;
      var next = searchInput.value.trim();
      if (next === query) return;
      query = next;
      load(false);
    }
    searchInput.addEventListener("input", function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(search, 300);
    });
    // Enter in the search field starts the search; it does not send the dialog.
    searchInput.addEventListener("keydown", function (ev) {
      if (ev.key !== "Enter") return;
      ev.preventDefault();
      search();
    });
    return load(false);
  }

  function selectedDocuments(opts, box) {
    if (opts && opts.documentId) return [String(opts.documentId)];
    return Array.prototype.slice.call(box.querySelectorAll('input[name="doc"]:checked')).map(function (x) { return x.value; });
  }

  // The result of POST /submissions: each item with its theme and the state of each target (design Section 9.2 rule 8).
  function submissionResultHtml(submission) {
    var rows = [];
    ((submission && submission.items) || []).forEach(function (item) {
      if (item.code === "no_theme") {
        rows.push({ document: item.document, theme: t("kb.dlg.result_no_theme"), kb: "", state: "" });
        return;
      }
      if (!(item.targets || []).length) rows.push({ document: item.document, theme: item.theme_label || item.theme, kb: t("kb.dlg.result_no_target"), state: "" });
      (item.targets || []).forEach(function (target) {
        // A target on another server (federation design Section 12.1) has its server after the label; its state is the decision of that server.
        var kb = target.remote ? t("kb.dlg.remote_kb_label", { label: target.label || target.actor, server: target.server }) : (target.label || target.knowledge_base);
        rows.push({ document: item.document, theme: item.theme_label || item.theme || "—", kb: kb, state: stateLabel(target.state) + (target.reason ? " (" + errText({ error: target.reason }) + ")" : "") });
      });
    });
    return ui().renderTable({
      caption: t("kb.dlg.result_title"),
      columns: [
        { label: t("kb.dlg.col_document"), key: "document" },
        { label: t("kb.dlg.col_theme"), key: "theme" },
        { label: t("kb.dlg.col_kb"), key: "kb" },
        { label: t("kb.dlg.col_state"), key: "state" }
      ],
      rows: rows
    });
  }

  // Dry run of the automatic rule (design Section 9.9 rule 6): POST /api/auto-rule/dry-run with the body of the rule. ``opts.onActivate`` turns the rule on after the person saw the plan.
  function openAutoRuleDryRunDialog(opts) {
    var payload = (opts && opts.payload) || {};
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML = '<p class="claire-hint">' + esc(t("kb.dlg.dry_run_loading")) + "</p>";
    var canActivate = !!(opts && typeof opts.onActivate === "function");

    return ui().openDialog({
      title: t("kb.dlg.dry_run_title"),
      icon: "bolt",
      bodyEl: box,
      submitLabel: canActivate ? t("kb.dlg.dry_run_activate") : null,
      cancelLabel: t("common.action.close"),
      onOpen: function (_dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        ui().request("/api/auto-rule/dry-run", { method: "POST", body: payload }).then(function (res) {
          if (!res.ok || !res.data) {
            box.innerHTML = '<div class="claire-note warn">' + esc(errText(res)) + "</div>";
            return;
          }
          if (submitBtn) submitBtn.disabled = false;
          var targets = res.data.targets || [];
          var count = targets.reduce(function (sum, target) { return sum + (target.documents || []).length; }, 0);
          var h = "";
          if (!res.data.server_switch) h += '<div class="claire-note warn"><span>' + esc(t("kb.dlg.dry_run_server_off")) + "</span></div>";
          // Targets on other servers (federation design Section 12.3): "remote_state" is "on", or the reason that the rule has none now.
          if (res.data.remote_state && res.data.remote_state !== "on") h += '<div class="claire-note"><span>' + esc(t("kb.dlg.dry_run_remote_" + res.data.remote_state)) + "</span></div>";
          var sending = targets.filter(function (target) { return target.mode !== "closed"; });
          h += '<div class="claire-note ok"><span>' + esc(t("kb.dlg.dry_run_summary", { count: count, targets: sending.length })) + "</span></div>";
          h += ui().renderTable({
            caption: t("kb.dlg.dry_run_title"),
            columns: [
              { label: t("kb.dlg.col_kb"), render: function (r) {
                // A shared knowledge base of another server: its label with a link to its page on that server, and its server.
                if (r.remote) return (/^https?:\/\//.test(String(r.url || "")) ? '<a href="' + esc(r.url) + '" target="_blank" rel="noopener noreferrer">' + esc(t("kb.dlg.remote_kb_label", { label: r.label || r.actor, server: r.server })) + "</a>" : esc(t("kb.dlg.remote_kb_label", { label: r.label || r.actor, server: r.server })));
                return '<a href="/kb/' + esc(r.knowledge_base) + '/">' + esc(r.label || r.knowledge_base) + "</a>";
              } },
              { label: t("kb.dlg.col_mode"), render: function (r) {
                // "closed": the server of the target accepts no automatic contributions now; nothing goes there.
                return '<span class="claire-tag ' + (r.mode === "closed" ? "warn" : "acc") + '">' + esc(t("kb.dlg.mode_" + r.mode)) + "</span>" + (r.mode === "closed" ? ' <span class="claire-hint">' + esc(t("kb.dlg.mode_closed_hint")) + "</span>" : "");
              } },
              { label: t("kb.dlg.col_docs"), render: function (r) { return (r.documents || []).map(function (d) { return esc(d.title || d.document) + (d.score != null ? ' <span class="claire-hint">(' + esc(Number(d.score).toFixed(2)) + ")</span>" : ""); }).join("<br>"); } }
            ],
            rows: targets
          });
          box.innerHTML = h;
          ui().enhance(box);
        });
      },
      onSubmit: function () {
        if (canActivate) opts.onActivate();
        return true;
      }
    });
  }

  // Direct contribution (design Section 9.1): POST /kb/{number}/contributions with one document of the personal knowledge base. An account contributes directly only where it is an active member, thus the list is GET /kb?joined=true. ``opts.kbNumber`` selects the target without the list.
  // A shared knowledge base of another server where the account has the role collaborator (a grant in its term, GET /api/kb/remote-grants; federation design Section 12.2 rule 4) is a direct target too: POST /submissions with {"documents": [...], "remote_knowledge_base": <id>}. The other server decides; the submission page shows its decision.
  function openNoThemePicker(opts) {
    opts = opts || {};
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML = '<p class="claire-hint">' + esc(t(opts.kbNumber ? "kb.dlg.direct_desc" : "kb.dlg.no_theme_desc")) + "</p>" +
      '<div id="claire-direct-docs"></div><div id="claire-no-theme-list"></div><div id="claire-remote-kb-list"></div><div id="claire-direct-msg" class="claire-note warn" hidden></div><div id="claire-direct-result"></div>';
    var docBox = box.querySelector("#claire-direct-docs");
    var listBox = box.querySelector("#claire-no-theme-list");
    var remoteBox = box.querySelector("#claire-remote-kb-list");
    var directResult = box.querySelector("#claire-direct-result");
    var msgBox = box.querySelector("#claire-direct-msg");

    var createBtn = document.createElement("button");
    createBtn.type = "button";
    createBtn.className = "claire-btn sec";
    createBtn.textContent = t("campaign.make_kb_link");

    function contribute(kbNumber, button, dlg) {
      var documents = selectedDocuments(opts, docBox);
      if (!documents.length) {
        msgBox.textContent = t("kb.dlg.need_document");
        msgBox.hidden = false;
        return;
      }
      button.disabled = true;
      msgBox.hidden = true;
      var failures = [];
      var chain = Promise.resolve();
      documents.forEach(function (documentId) {
        chain = chain.then(function () {
          return ui().request("/kb/" + encodeURIComponent(kbNumber) + "/contributions", { method: "POST", body: { document: documentId } }).then(function (res) {
            if (!res.ok) failures.push(documentId + ": " + errText(res));
            else if (res.data && res.data.contribution && res.data.contribution.state === "failed") failures.push(documentId + ": " + errText({ error: res.data.contribution.reason }));
          });
        });
      });
      chain.then(function () {
        button.disabled = false;
        if (failures.length) {
          msgBox.textContent = failures.join(" · ");
          msgBox.hidden = false;
        }
        if (failures.length < documents.length) {
          ui().toast(t("kb.dlg.contrib_done"), "ok");
          if (opts.onSubmitted) opts.onSubmitted();
          if (!failures.length) dlg.close("submitted");
        }
      });
    }

    function contributeRemote(remoteId, button) {
      var documents = selectedDocuments(opts, docBox);
      if (!documents.length) {
        msgBox.textContent = t("kb.dlg.need_document");
        msgBox.hidden = false;
        return;
      }
      button.disabled = true;
      msgBox.hidden = true;
      ui().request("/submissions", { method: "POST", body: { documents: documents, remote_knowledge_base: Number(remoteId) } }).then(function (res) {
        button.disabled = false;
        if (!res.ok) {
          msgBox.textContent = errText(res) + (res.data && res.data.limit ? " (" + res.data.limit + ")" : "");
          msgBox.hidden = false;
          return;
        }
        // The dialog stays open with the state of each target: the offer waits for the decision of the other server, or the target was skipped with a reason.
        directResult.innerHTML = submissionResultHtml(res.data.submission);
        ui().enhance(directResult);
        ui().toast(t("kb.dlg.contrib_done"), "ok");
        if (opts.onSubmitted) opts.onSubmitted(res.data);
      });
    }

    function drawRemoteTargets(grants) {
      var targets = (grants || []).filter(function (g) { return g.state === "active" && g.remote_id != null; });
      if (!targets.length) return;
      remoteBox.innerHTML = ui().renderTable({
        caption: t("kb.dlg.remote_targets"),
        columns: [
          { label: t("kb.dlg.col_kb"), render: function (g) { return "<strong>" + esc(t("kb.dlg.remote_kb_label", { label: g.label || g.knowledge_base, server: g.server })) + "</strong>"; } },
          { label: t("kb.pub.col_role"), render: function () { return esc(t("kb.role.collaborator")); } },
          { label: "", actions: true, render: function (g) { return '<button type="button" class="claire-btn pri claire-btn-sm" data-pick-remote-kb="' + esc(g.remote_id) + '">' + esc(t("kb.dlg.submit_here")) + "</button>"; } }
        ],
        rows: targets
      });
      remoteBox.addEventListener("click", function (ev) {
        var btn = ev.target && ev.target.closest ? ev.target.closest("[data-pick-remote-kb]") : null;
        if (btn) contributeRemote(btn.getAttribute("data-pick-remote-kb"), btn);
      });
    }

    return ui().openDialog({
      title: t(opts.kbNumber ? "kb.dlg.direct_title" : "kb.dlg.no_theme_title"),
      icon: "folder",
      bodyEl: box,
      extraFooterButtons: opts.kbNumber ? [] : [createBtn],
      submitLabel: opts.kbNumber ? t("kb.dlg.submit_here") : null,
      onOpen: function (dlg) {
        createBtn.addEventListener("click", function () {
          dlg.close("create");
          openCreateKbDialog({ onCreated: function () { if (opts.onSubmitted) opts.onSubmitted(); } });
        });
        if (opts.documentId) docBox.innerHTML = '<div class="claire-srow"><div class="claire-srow-info"><strong>' + esc(opts.documentTitle || opts.documentId) + "</strong></div></div>";
        else loadDocumentChoices(docBox, opts.homeHref);
        if (opts.kbNumber) return;
        ui().request("/api/kb/remote-grants").then(function (res) { if (res.ok && res.data) drawRemoteTargets(res.data.grants); });
        ui().request("/kb?joined=true").then(function (res) {
          var items = (res.ok && res.data && res.data.knowledge_bases) || [];
          if (!res.ok || !items.length) {
            listBox.innerHTML = '<div class="claire-note warn">' + esc(res.ok ? t("kb.dlg.no_kbs_yet") : errText(res)) + "</div>";
            return;
          }
          listBox.innerHTML = ui().renderTable({
            caption: t("kb.dlg.no_theme_title"),
            columns: [
              { label: t("kb.dlg.col_kb"), render: function (r) { return "<strong>" + esc(r.label) + "</strong> " + typeTag(r.knowledge_type); } },
              { label: t("kb.pub.col_role"), render: function (r) { return esc(t("kb.role." + r.role)); } },
              { label: "", actions: true, render: function (r) { return r.state === "archived" ? esc(stateLabel("archived")) : '<button type="button" class="claire-btn pri claire-btn-sm" data-pick-kb="' + esc(r.number) + '">' + esc(t("kb.dlg.submit_here")) + "</button>"; } }
            ],
            rows: items
          });
          listBox.addEventListener("click", function (ev) {
            var btn = ev.target && ev.target.closest ? ev.target.closest("[data-pick-kb]") : null;
            if (btn) contribute(btn.getAttribute("data-pick-kb"), btn, dlg);
          });
        });
      },
      onSubmit: function (dlg, _b, submitBtn) {
        contribute(opts.kbNumber, submitBtn, dlg);
        return false;
      }
    });
  }

  // Contribution to a theme (design Section 9.2): POST /submissions with {"documents": [...], "theme"?: "<number>"}. Without ``theme`` the server routes each document by its theme match.
  function openContribPreview(opts) {
    opts = opts || {};
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML = '<p class="claire-hint">' + esc(t("kb.dlg.contrib_preview_desc")) + "</p>" +
      '<div id="claire-contrib-docs"></div><div id="claire-contrib-targets"></div>';
    var docBox = box.querySelector("#claire-contrib-docs");
    var resultBox = box.querySelector("#claire-contrib-targets");
    var done = false;

    var pickDirectBtn = document.createElement("button");
    pickDirectBtn.type = "button";
    pickDirectBtn.className = "claire-btn sec";
    pickDirectBtn.textContent = t("kb.dlg.pick_kb_direct");

    return ui().openDialog({
      title: t("kb.dlg.contrib_title"),
      icon: "merge",
      bodyEl: box,
      extraFooterButtons: [pickDirectBtn],
      submitLabel: t(opts.theme ? "campaign.contribute_action" : "kb.dlg.contrib_submit_theme"),
      onOpen: function (dlg) {
        pickDirectBtn.addEventListener("click", function () {
          dlg.close("direct");
          openNoThemePicker(opts);
        });
        if (opts.documentId) docBox.innerHTML = '<div class="claire-srow"><div class="claire-srow-info"><strong>' + esc(opts.documentTitle || opts.documentId) + "</strong></div></div>";
        else loadDocumentChoices(docBox, opts.homeHref);
        if (opts.theme) resultBox.innerHTML = '<div class="claire-note ok"><span>' + esc(t("kb.dlg.target_theme_label", { label: opts.themeLabel || opts.theme })) + "</span></div>";
      },
      onSubmit: function (_dlg, _b, submitBtn) {
        if (done) return true;
        var documents = selectedDocuments(opts, docBox);
        if (!documents.length) {
          resultBox.innerHTML = '<div class="claire-note warn">' + esc(t("kb.dlg.need_document")) + "</div>";
          return false;
        }
        if (submitBtn) submitBtn.disabled = true;
        var payload = { documents: documents };
        if (opts.theme) payload.theme = String(opts.theme);
        return ui().request("/submissions", { method: "POST", body: payload }).then(function (res) {
          if (submitBtn) submitBtn.disabled = false;
          if (!res.ok) {
            resultBox.innerHTML = '<div class="claire-note warn">' + esc(errText(res)) + (res.data && res.data.limit ? " (" + esc(res.data.limit) + ")" : "") + "</div>";
            return false;
          }
          // The dialog stays open with the state of each target. An item without a theme has the code "no_theme": the person contributes it directly or makes a shared knowledge base (Section 9.2 rule 3).
          done = true;
          resultBox.innerHTML = submissionResultHtml(res.data.submission);
          ui().enhance(resultBox);
          if (submitBtn) submitBtn.textContent = t("common.action.close");
          ui().toast(t("kb.dlg.contrib_done"), "ok");
          if (opts.onSubmitted) opts.onSubmitted(res.data);
          return false;
        });
      }
    });
  }

  // POST /kb: label, knowledge_type (required), description, seed_entities (at most 20). The embedding model is the fixed model of the server: the request has no model field.
  function openCreateKbDialog(opts) {
    var box = document.createElement("form");
    box.className = "claire-panel-inner";
    box.noValidate = true;
    var defaultType = (opts && opts.defaultType) === "fictional" ? "fictional" : "real";
    box.innerHTML =
      '<div id="claire-similar-kbs" class="claire-panel-inner"><p class="claire-hint">' + esc(t("kb.dlg.loading_similar")) + "</p></div>" +
      '<div class="claire-field">' +
      '  <label class="claire-field-label" for="newkb-label">' + esc(t("kb.dlg.field_name")) + "</label>" +
      '  <input id="newkb-label" name="label" class="claire-input" required maxlength="80" data-validate data-error-id="newkb-label-err" aria-invalid="false"/>' +
      '  <span id="newkb-label-err" class="claire-error" hidden></span>' +
      "</div>" +
      '<fieldset class="claire-field" style="border:0;padding:0;margin:0"><legend class="claire-field-label">' + esc(t("kb.dlg.field_type")) + "</legend>" +
      '  <div class="claire-chips">' +
      '    <label class="claire-chip"><input type="radio" name="knowledge_type" value="real"' + (defaultType === "real" ? " checked" : "") + "/><span>" + esc(t("kb.dlg.type_real")) + "</span></label>" +
      '    <label class="claire-chip"><input type="radio" name="knowledge_type" value="fictional"' + (defaultType === "fictional" ? " checked" : "") + "/><span>" + esc(t("kb.dlg.type_fictional")) + "</span></label>" +
      "  </div>" +
      "</fieldset>" +
      '<div class="claire-field">' +
      '  <label class="claire-field-label" for="newkb-desc">' + esc(t("kb.dlg.field_desc")) + "</label>" +
      '  <textarea id="newkb-desc" name="description" class="claire-textarea" maxlength="400"></textarea>' +
      "</div>" +
      '<div class="claire-field">' +
      '  <label class="claire-field-label" for="newkb-seeds">' + esc(t("kb.dlg.field_seeds")) + "</label>" +
      '  <input id="newkb-seeds" name="seed_entities" class="claire-input" data-validate data-error-id="newkb-seeds-err" aria-invalid="false"/>' +
      '  <span id="newkb-seeds-err" class="claire-error" hidden></span>' +
      "</div>" +
      '<div id="newkb-form-err" class="claire-note warn" hidden></div>';

    return ui().openDialog({
      title: t("kb.dlg.create_title"),
      icon: "plus",
      bodyEl: box,
      submitLabel: t("campaign.make_kb_link"),
      onOpen: function (_dlg, _b, submitBtn) {
        // KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 4: the shared knowledge bases of this server and of other servers near the subject, before the account makes a new one. GET /api/kb/similar gives "knowledge_bases" (remote, server, number, label, knowledge_type, url, score) and "matched" (false: the account typed no text, and the list has the knowledge bases of the type).
        var similarSeq = 0;
        var similarTimer = null;
        function loadExisting() {
          var knowledgeType = (box.querySelector('input[name="knowledge_type"]:checked') || {}).value || defaultType;
          var query = "type=" + encodeURIComponent(knowledgeType) + "&label=" + encodeURIComponent(box.querySelector("#newkb-label").value.trim().slice(0, 200));
          seedList(box.querySelector("#newkb-seeds").value).slice(0, 20).forEach(function (seed) { query += "&seed=" + encodeURIComponent(seed.slice(0, 200)); });
          var seq = ++similarSeq;
          ui().request("/api/kb/similar?" + query).then(function (res) {
            if (seq !== similarSeq) return;
            var host = box.querySelector("#claire-similar-kbs");
            var items = (res.ok && res.data && res.data.knowledge_bases) || [];
            if (!items.length) {
              host.innerHTML = '<p class="claire-hint">' + esc(t(res.ok && res.data && res.data.matched ? "kb.dlg.similar_no_match" : "kb.dlg.similar_none")) + "</p>";
              return;
            }
            host.innerHTML = '<div class="claire-note"><span>' + esc(t(res.data.matched ? "kb.dlg.similar_matched" : "kb.dlg.similar_exists")) + "</span></div>" +
              '<div class="claire-tags">' + items.map(function (it) {
                var cls = "claire-tag " + (it.knowledge_type === "fictional" ? "fic" : "acc");
                if (it.remote) return '<a class="' + cls + '" data-similar-remote="' + esc(it.server) + '" href="' + esc(it.url) + '" target="_blank" rel="noopener noreferrer">' + esc(it.label) + " · " + esc(it.server) + "</a>";
                return '<a class="' + cls + '" href="/kb/' + esc(it.number) + '/">' + esc(it.label) + "</a>";
              }).join("") + "</div>";
          });
        }
        function loadExistingSoon() {
          if (similarTimer) clearTimeout(similarTimer);
          similarTimer = setTimeout(loadExisting, 300);
        }
        loadExisting();
        box.querySelectorAll('input[name="knowledge_type"]').forEach(function (input) { input.addEventListener("change", loadExisting); });
        box.querySelector("#newkb-label").addEventListener("input", loadExistingSoon);
        box.querySelector("#newkb-seeds").addEventListener("input", loadExistingSoon);
        var seedsEl = box.querySelector("#newkb-seeds");
        seedsEl.addEventListener("input", function () {
          seedsEl.setCustomValidity(seedList(seedsEl.value).length > 20 ? t("kb.dlg.err_seeds_max") : "");
        });
        box.addEventListener("submit", function (ev) {
          ev.preventDefault();
          if (submitBtn) submitBtn.click();
        });
      },
      onSubmit: function (_dlg, _b, submitBtn) {
        var labelEl = box.querySelector("#newkb-label");
        var seedsEl = box.querySelector("#newkb-seeds");
        var seeds = seedList(seedsEl.value);
        seedsEl.setCustomValidity(seeds.length > 20 ? t("kb.dlg.err_seeds_max") : "");
        if (!box.reportValidity()) return false;
        if (submitBtn) submitBtn.disabled = true;
        var payload = {
          label: labelEl.value.trim(),
          knowledge_type: (box.querySelector('input[name="knowledge_type"]:checked') || {}).value,
          description: box.querySelector("#newkb-desc").value.trim()
        };
        if (seeds.length) payload.seed_entities = seeds;
        return ui().request("/kb", { method: "POST", body: payload }).then(function (res) {
          var errBox = box.querySelector("#newkb-form-err");
          if (!res.ok || !res.data || !res.data.knowledge_base) {
            if (submitBtn) submitBtn.disabled = false;
            errBox.textContent = errText(res);
            errBox.hidden = false;
            return false;
          }
          ui().toast(t("kb.dlg.create_done"), "ok");
          if (opts && opts.onCreated) opts.onCreated(res.data.knowledge_base);
          return true;
        });
      }
    });
  }

  // POST /kb/{number}/forks (design Section 9.8).
  function openForkKbDialog(opts) {
    var kb = (opts && opts.kb) || {};
    return ui().openDialog({
      title: t("kb.dlg.fork_title"),
      icon: "fork",
      bodyHtml: "<p>" + esc(t("kb.dlg.fork_desc", { label: kb.label || kb.number })) + "</p>" +
        '<div id="claire-fork-err" class="claire-note warn" hidden></div>',
      submitLabel: t("kb.dlg.fork_confirm"),
      onSubmit: function (_dlg, body, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        return ui().request("/kb/" + encodeURIComponent(kb.number) + "/forks", { method: "POST", body: {} }).then(function (res) {
          if (!res.ok || !res.data || !res.data.knowledge_base) {
            if (submitBtn) submitBtn.disabled = false;
            var errEl = body.querySelector("#claire-fork-err");
            errEl.textContent = errText(res);
            errEl.hidden = false;
            return false;
          }
          ui().toast(t("kb.dlg.fork_done", { documents: res.data.documents || 0 }), "ok");
          if (opts && opts.onForked) opts.onForked(res.data.knowledge_base);
          return true;
        });
      }
    });
  }

  function reasonSelectHtml(id) {
    return '<div class="claire-field"><label class="claire-field-label" for="' + id + '">' + esc(t("kb.dlg.remove_reason")) + "</label>" +
      '<select id="' + id + '" name="reason" class="claire-select" required>' +
      REASON_CODES.map(function (code) { return '<option value="' + code + '">' + esc(t("kb.reason." + code)) + "</option>"; }).join("") +
      "</select></div>";
  }

  // POST /kb/{number}/contributions/{contribution}/remove with a reason code (design Section 9.6): the copy is purged and a tombstone stays.
  function openRemoveContributionDialog(opts) {
    var box = document.createElement("form");
    box.className = "claire-panel-inner";
    box.innerHTML = '<div class="claire-note danger"><span>' + esc(t("kb.dlg.remove_warn")) + "</span></div>" + reasonSelectHtml("rm-reason") + '<div id="rm-err" class="claire-note warn" hidden></div>';

    return ui().openDialog({
      title: t("kb.dlg.remove_title"),
      icon: "trash",
      danger: true,
      bodyEl: box,
      submitLabel: t("kb.dlg.remove_submit"),
      onSubmit: function (_dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        return ui().request("/kb/" + encodeURIComponent(opts.kbNumber) + "/contributions/" + encodeURIComponent(opts.contribNumber) + "/remove", {
          method: "POST",
          body: { reason: box.querySelector("#rm-reason").value }
        }).then(function (res) {
          if (!res.ok) {
            if (submitBtn) submitBtn.disabled = false;
            var errEl = box.querySelector("#rm-err");
            errEl.textContent = errText(res);
            errEl.hidden = false;
            return false;
          }
          ui().toast(t("kb.dlg.remove_done"), "ok");
          if (opts && opts.onRemoved) opts.onRemoved(res.data);
          return true;
        });
      }
    });
  }

  // PATCH /kb/{number}/contributions/{contribution} with {"action": "reject", "reason": "<code>"}: a reject needs a reason code (design Section 9.4).
  function openRejectContributionDialog(opts) {
    var box = document.createElement("form");
    box.className = "claire-panel-inner";
    box.innerHTML = reasonSelectHtml("rj-reason") + '<div id="rj-err" class="claire-note warn" hidden></div>';

    return ui().openDialog({
      title: t("kb.dlg.reject_title"),
      icon: "block",
      danger: true,
      bodyEl: box,
      submitLabel: t("kb.dlg.reject_submit"),
      onSubmit: function (_dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        return ui().request("/kb/" + encodeURIComponent(opts.kbNumber) + "/contributions/" + encodeURIComponent(opts.contribNumber), {
          method: "PATCH",
          body: { action: "reject", reason: box.querySelector("#rj-reason").value }
        }).then(function (res) {
          if (!res.ok) {
            if (submitBtn) submitBtn.disabled = false;
            var errEl = box.querySelector("#rj-err");
            errEl.textContent = errText(res);
            errEl.hidden = false;
            return false;
          }
          if (opts && opts.onRejected) opts.onRejected(res.data);
          return true;
        });
      }
    });
  }

  // POST /api/admin/themes/{number}/merge with {"into": "<number>"} (design Section 8.3). The two themes have one knowledge type.
  function openMergeThemeDialog(opts) {
    var src = (opts && opts.sourceTheme) || {};
    var sameType = ((opts && opts.themes) || []).filter(function (th) {
      return String(th.number) !== String(src.number) && th.knowledge_type === src.knowledge_type && th.state !== "merged";
    });
    var box = document.createElement("form");
    box.className = "claire-panel-inner";
    if (!sameType.length) {
      box.innerHTML = '<div class="claire-note warn">' + esc(t("kb.dlg.merge_none")) + "</div>";
    } else {
      box.innerHTML =
        '<div class="claire-note warn">' + esc(t("kb.dlg.merge_warn", { label: src.label || src.number })) + "</div>" +
        '<div class="claire-field"><label class="claire-field-label" for="merge-into">' + esc(t("kb.dlg.merge_target")) + "</label>" +
        '<select id="merge-into" class="claire-select" required>' +
        sameType.map(function (th) { return '<option value="' + esc(th.number) + '">' + esc(th.label) + " (" + esc(th.number) + ")</option>"; }).join("") +
        "</select></div>" +
        '<div id="merge-err" class="claire-note warn" hidden></div>';
    }
    return ui().openDialog({
      title: t("kb.dlg.merge_title"),
      icon: "merge",
      danger: true,
      bodyEl: box,
      submitLabel: sameType.length ? t("kb.dlg.merge_submit") : null,
      onSubmit: function (_dlg, _b, submitBtn) {
        if (!sameType.length) return true;
        if (submitBtn) submitBtn.disabled = true;
        return ui().request("/api/admin/themes/" + encodeURIComponent(src.number) + "/merge", {
          method: "POST",
          body: { into: box.querySelector("#merge-into").value }
        }).then(function (res) {
          if (!res.ok) {
            if (submitBtn) submitBtn.disabled = false;
            var errEl = box.querySelector("#merge-err");
            errEl.textContent = errText(res);
            errEl.hidden = false;
            return false;
          }
          ui().toast(t("kb.dlg.merge_done"), "ok");
          if (opts && opts.onMerged) opts.onMerged(res.data);
          return true;
        });
      }
    });
  }

  // DELETE /kb/{number}/ (the admin, with a recent sign-in): the first request gives the plan only; "?apply=true" deletes (design Section 10.1). The request helper opens the re-authentication dialog for 401 "reauth_required" and sends the request again.
  function openDeleteKbDialog(opts) {
    var kbNumber = opts && opts.kbNumber;
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML = '<p class="claire-hint">' + esc(t("kb.dlg.delete_dry_loading")) + "</p>";
    var canApply = false;

    return ui().openDialog({
      title: t("kb.dlg.delete_title", { label: (opts && opts.kbLabel) || kbNumber }),
      icon: "trash",
      danger: true,
      bodyEl: box,
      submitLabel: t("kb.dlg.delete_apply"),
      onOpen: function (_dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        ui().request("/kb/" + encodeURIComponent(kbNumber) + "/", { method: "DELETE" }).then(function (res) {
          if (!res.ok || !res.data || !res.data.would_delete) {
            box.innerHTML = '<div class="claire-note warn">' + esc(errText(res)) + "</div>";
            return;
          }
          canApply = true;
          if (submitBtn) submitBtn.disabled = false;
          var wd = res.data.would_delete;
          box.innerHTML =
            '<div class="claire-note danger"><span>' + esc(t("kb.dlg.delete_impact")) + "</span></div>" +
            '<div class="claire-kpis">' +
            '<div class="claire-kpi"><strong>' + esc(wd.documents || 0) + "</strong><span>" + esc(t("kb.dlg.col_docs")) + "</span></div>" +
            '<div class="claire-kpi"><strong>' + esc(wd.entities || 0) + "</strong><span>" + esc(t("kb.dlg.col_entities")) + "</span></div>" +
            '<div class="claire-kpi"><strong>' + esc(wd.relations || 0) + "</strong><span>" + esc(t("kb.dlg.col_relations")) + "</span></div>" +
            '<div class="claire-kpi"><strong>' + esc(wd.members || 0) + "</strong><span>" + esc(t("kb.dlg.col_members")) + "</span></div>" +
            '<div class="claire-kpi"><strong>' + esc(wd.contributions || 0) + "</strong><span>" + esc(t("kb.dlg.col_contributions")) + "</span></div>" +
            "</div>";
        });
      },
      onSubmit: function (_dlg, _b, submitBtn) {
        if (!canApply) return false;
        if (submitBtn) submitBtn.disabled = true;
        return ui().request("/kb/" + encodeURIComponent(kbNumber) + "/?apply=true", { method: "DELETE" }).then(function (res) {
          if (!res.ok) {
            if (submitBtn) submitBtn.disabled = false;
            ui().toast(errText(res), "danger");
            return false;
          }
          ui().toast(t("kb.dlg.delete_done"), "ok");
          if (opts && opts.onDeleted) opts.onDeleted(res.data);
          return true;
        });
      }
    });
  }

  // PATCH /kb/{number}/ with "knowledge_type" (the capability "settings": a moderator or the admin). A moderator or the admin corrects a wrong knowledge type with it. The server removes the theme rows of the knowledge base; the next judgement run places it in themes of the new type.
  function openKnowledgeTypeDialog(opts) {
    var kbNumber = opts && opts.kbNumber;
    var current = (opts && opts.current) === "fictional" ? "fictional" : "real";
    var box = document.createElement("div");
    box.className = "claire-panel-inner";
    box.innerHTML =
      '<fieldset class="claire-field" style="border:0;padding:0;margin:0"><legend class="claire-field-label">' + esc(t("kb.dlg.field_type")) + "</legend>" +
      '  <div class="claire-chips">' +
      '    <label class="claire-chip"><input type="radio" name="kb_type_change" value="real"' + (current === "real" ? " checked" : "") + "/><span>" + esc(t("kb.dlg.type_real")) + "</span></label>" +
      '    <label class="claire-chip"><input type="radio" name="kb_type_change" value="fictional"' + (current === "fictional" ? " checked" : "") + "/><span>" + esc(t("kb.dlg.type_fictional")) + "</span></label>" +
      "  </div>" +
      "</fieldset>" +
      '<div class="claire-note warn"><span>' + esc(t("kb.dlg.type_change_effect")) + "</span></div>";

    function chosen() {
      var inp = box.querySelector('input[name="kb_type_change"]:checked');
      return inp ? inp.value : current;
    }

    return ui().openDialog({
      title: t("kb.dlg.type_change_title", { label: (opts && opts.kbLabel) || kbNumber }),
      icon: "edit",
      bodyEl: box,
      submitLabel: t("kb.dlg.type_change_apply"),
      onOpen: function (_dlg, _b, submitBtn) {
        if (submitBtn) submitBtn.disabled = true;
        box.addEventListener("change", function () { if (submitBtn) submitBtn.disabled = chosen() === current; });
      },
      onSubmit: function (_dlg, _b, submitBtn) {
        var value = chosen();
        if (value === current) return false;
        if (submitBtn) submitBtn.disabled = true;
        return ui().request("/kb/" + encodeURIComponent(kbNumber) + "/", { method: "PATCH", body: { knowledge_type: value } }).then(function (res) {
          if (!res.ok) {
            if (submitBtn) submitBtn.disabled = false;
            ui().toast(errText(res), "danger");
            return false;
          }
          ui().toast(t("kb.dlg.type_change_done"), "ok");
          if (opts && opts.onChanged) opts.onChanged((res.data && res.data.knowledge_base) || null);
          return true;
        });
      }
    });
  }

  // The membership of the requester in a shared knowledge base. ``kb`` is the item of GET /kb or of GET /kb/{number}/ (JSON): "membership_state" is "active", "pending", "banned", or null; "role" is the role of an active membership.
  function membershipOf(kb) {
    var state = (kb && kb.membership_state) || (kb && kb.role ? "active" : "");
    return { state: state, role: state === "active" ? ((kb && kb.role) || "member") : "" };
  }

  // The actions of a signed-in account on one shared knowledge base: join and leave (design Section 10.2), direct contribution (Section 9.1), fork (Section 9.8), and the link to the review page. The public page of the knowledge base and the workspace use the same buttons. ``opts.isAdmin``: the requester is the admin.
  function kbActionsHtml(kb, opts) {
    var membership = membershipOf(kb);
    var archived = kb.state === "archived";
    var small = opts && opts.small ? " claire-btn-sm" : "";
    var h = "";
    if (membership.role) {
      if (!archived) h += '<button type="button" class="claire-btn pri' + small + '" data-kb-act="direct">' + ui().icon("merge") + " " + esc(t("kb.pub.kb_direct_contribute")) + "</button>";
      h += '<button type="button" class="claire-btn sec' + small + '" data-kb-act="leave">' + esc(t("kb.pub.kb_leave")) + "</button>";
    } else if (membership.state === "pending") {
      h += '<button type="button" class="claire-btn sec' + small + '" data-kb-act="leave">' + esc(t("kb.pub.kb_withdraw_join")) + "</button>";
    } else if (!archived && membership.state !== "banned") {
      h += '<button type="button" class="claire-btn pri' + small + '" data-kb-act="join">' + esc(t(kb.join_policy === "open" ? "kb.pub.kb_join" : "kb.pub.kb_join_request")) + "</button>";
    }
    h += '<button type="button" class="claire-btn' + small + '" data-kb-act="fork">' + ui().icon("fork") + " " + esc(t("kb.dlg.fork_confirm")) + "</button>";
    if ((opts && opts.isAdmin) || membership.role === "moderator" || membership.role === "collaborator") h += '<a class="claire-btn sec' + small + '" href="/settings/kb/' + esc(encodeURIComponent(kb.number)) + '">' + esc(t("kb.pub.kb_review_manage")) + "</a>";
    return h;
  }

  // The notice of a join request that waits, or of a ban. Without one of them: no text.
  function kbMembershipNoteHtml(kb) {
    var state = membershipOf(kb).state;
    if (state === "pending") return '<div class="claire-note ok"><span>' + esc(t("kb.pub.kb_join_pending")) + "</span></div>";
    if (state === "banned") return '<div class="claire-note danger"><span>' + esc(t("kb.pub.kb_banned")) + "</span></div>";
    return "";
  }

  // The handlers of the buttons of ``kbActionsHtml`` in ``host``. ``opts.homeHref`` is the path of the personal knowledge base of the requester; ``opts.onChanged`` runs after a membership change or a contribution; ``opts.onForked`` gets the new knowledge base.
  function bindKbActions(host, kb, opts) {
    opts = opts || {};
    var membershipUrl = "/kb/" + encodeURIComponent(kb.number) + "/membership";
    function changed() { if (opts.onChanged) opts.onChanged(); }
    host.querySelectorAll("[data-kb-act]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var act = btn.getAttribute("data-kb-act");
        if (act === "join" || act === "leave") {
          btn.disabled = true;
          // POST /kb/{number}/membership: the join policy "open" gives an active membership, "approval" a pending one. DELETE: leave, or withdraw the join request.
          ui().request(membershipUrl, act === "join" ? { method: "POST", body: {} } : { method: "DELETE" }).then(function (res) {
            btn.disabled = false;
            if (!res.ok) { ui().toast(errText(res), "danger"); return; }
            changed();
          });
        } else if (act === "direct") {
          openNoThemePicker({ kbNumber: kb.number, homeHref: opts.homeHref, onSubmitted: changed });
        } else if (act === "fork") {
          openForkKbDialog({ kb: kb, onForked: function (fork) { if (opts.onForked) opts.onForked(fork); } });
        }
      });
    });
  }

  root.ClaireKbDialogs = {
    REASON_CODES: REASON_CODES,
    typeTag: typeTag,
    stateLabel: stateLabel,
    seedList: seedList,
    membershipOf: membershipOf,
    kbActionsHtml: kbActionsHtml,
    kbMembershipNoteHtml: kbMembershipNoteHtml,
    bindKbActions: bindKbActions,
    openAutoRuleDryRunDialog: openAutoRuleDryRunDialog,
    openNoThemePicker: openNoThemePicker,
    openContribPreview: openContribPreview,
    openCreateKbDialog: openCreateKbDialog,
    openForkKbDialog: openForkKbDialog,
    openRemoveContributionDialog: openRemoveContributionDialog,
    openRejectContributionDialog: openRejectContributionDialog,
    openMergeThemeDialog: openMergeThemeDialog,
    openDeleteKbDialog: openDeleteKbDialog,
    openKnowledgeTypeDialog: openKnowledgeTypeDialog
  };
})(window);
