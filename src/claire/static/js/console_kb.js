/* Settings KB & Shared KB Review/Moderation console controller (/settings/kb, /settings/kb/{number}). */
(function (root) {
  "use strict";

  function ui() { return root.ClaireUi; }
  function dlgs() { return root.ClaireKbDialogs; }
  function t(key, params, fallback) { return ui().t(key, params, fallback); }
  function esc(v) { return ui().esc(v); }

  function renderSettingsKb(host, bs) {
    var activeTab = (bs && bs.initial_tab) || "personal";
    var state = { personal: null, kbs: [], subs: [], ruleData: null, rep: null, mutual: [] };

    function loadAll() {
      return Promise.all([
        ui().request("/kb?page=true&limit=50"),
        ui().request("/submissions"),
        ui().request("/api/auto-rule"),
        ui().request("/api/reputation"),
        ui().request("/api/mutual-collaborations")
      ]).then(function (all) {
        var kbRes = all[0], subRes = all[1], ruleRes = all[2], repRes = all[3], mutRes = all[4];
        state.personal = (kbRes.ok && kbRes.data && kbRes.data.personal_kb) || null;
        state.kbs = (kbRes.ok && kbRes.data && (kbRes.data.orderedItems || kbRes.data.items)) || [];
        if (!state.personal) {
          state.personal = state.kbs.find(function (k) { return k && k.is_personal; }) || null;
        }
        state.subs = (subRes.ok && subRes.data && (subRes.data.orderedItems || subRes.data.items || subRes.data.submissions)) || [];
        state.ruleData = (ruleRes.ok && ruleRes.data) || null;
        state.rep = (repRes.ok && repRes.data) || null;
        state.mutual = (mutRes.ok && mutRes.data && (mutRes.data.mutual_collaborations || mutRes.data.items)) || [];
        draw(all);
      });
    }

    function buildPersonalTab(kbUnavailable) {
      var acc = (bs && bs.account) || {};
      var pkb = state.personal;
      var pNum = pkb ? pkb.number : (bs && bs.default_theme_number) || "";
      var pLabel = (pkb && pkb.label) || acc.display_name || "Personal KB";
      var isPub = pkb ? !!pkb.is_public : false;
      var stats = (pkb && pkb.stats) || {};
      var moderated = state.kbs.filter(function (k) { return !k.is_personal && (k.role === "moderator" || k.role === "collaborator"); });

      var h = "";
      if (kbUnavailable) {
        h += '<div class="claire-note warn"><span>' + esc(t("kb.dlg.backend_pending")) + "</span></div>";
      }
      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("folder") + " " + esc(t("console.js.kb.personal_card")) + '</h3>' +
        (pNum ? '<a class="claire-btn claire-btn-sm" href="/">' + esc(t("nav.workspace", null, "Open workspace")) + "</a>" : "") + "</div>";
      h += '<div class="claire-srow"><div class="claire-srow-info"><strong>' + esc(pLabel) + (pNum ? ' <span class="claire-tag">#' + esc(pNum) + "</span>" : "") + '</strong>' +
        '<span>' + esc(t("kb.dlg.col_docs")) + ": " + esc(stats.documents || 0) + " · " + esc(t("kb.dlg.col_entities")) + ": " + esc(stats.entities || 0) + "</span></div>" +
        '<span class="claire-tag acc">' + esc(t("kb.dlg.type_real")) + "</span></div>";
      h += '<div class="claire-srow"><div class="claire-srow-info"><label for="kb-personal-public">' + esc(t("console.js.kb.public_toggle")) + "</label><span>" + esc(t("console.js.kb.legacy_migration_note")) + "</span></div>" +
        '<input id="kb-personal-public" type="checkbox" class="claire-switch" data-personal-public="' + esc(pNum) + '"' + (isPub ? " checked" : "") + (pNum ? "" : " disabled") + "/></div></div>";

      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("users") + " " + esc(t("console.js.kb.moderated_card")) + '</h3>' +
        '<button type="button" class="claire-btn pri claire-btn-sm" id="kb-create-btn">' + ui().icon("plus") + " " + esc(t("kb.dlg.create_btn")) + "</button></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.moderated_card"),
        columns: [
          { label: t("kb.dlg.col_kb"), render: function (r) { return '<a href="/kb/' + esc(r.number) + '/"><strong>' + esc(r.label) + '</strong></a> <span class="claire-tag ' + (r.knowledge_type === "fictional" ? "fic" : "acc") + '">' + esc(r.knowledge_type || "real") + "</span>"; } },
          { label: t("kb.pub.col_role"), render: function (r) { return '<span class="claire-tag ok">' + esc(r.role) + "</span>"; } },
          { label: t("kb.dlg.col_docs"), render: function (r) { return esc((r.stats && r.stats.documents) || 0); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (r) { return '<a class="claire-btn claire-btn-sm" href="/settings/kb/' + esc(r.number) + '">' + esc(t("kb.pub.kb_review_manage")) + "</a>"; } }
        ],
        rows: moderated
      });
      h += "</div>";
      return h;
    }

    function buildSubsTab() {
      var rows = [];
      state.subs.forEach(function (sub) {
        (sub.items || []).forEach(function (it) {
          (it.targets || []).forEach(function (tg) {
            rows.push({
              doc: it.title || it.document,
              kb: tg.knowledge_base,
              kbLabel: tg.label || ("#" + tg.knowledge_base),
              theme: it.theme_label || (it.theme ? "#" + it.theme : "—"),
              state: tg.state || "pending",
              approved: !!tg.approved,
              contrib: tg.contribution
            });
          });
        });
      });
      var h = '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("merge") + " " + esc(t("console.js.kb.subs_card")) + "</h3></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.subs_card"),
        columns: [
          { label: t("kb.dlg.col_docs"), render: function (r) { return "<strong>" + esc(r.doc) + "</strong>"; } },
          { label: t("kb.dlg.col_kb"), render: function (r) { return '<a href="/kb/' + esc(r.kb) + '/">' + esc(r.kbLabel) + "</a>"; } },
          { label: t("kb.pub.nav_themes"), render: function (r) { return esc(r.theme); } },
          { label: t("console.common.status", null, "Status"), render: function (r) {
            var cls = r.state === "approved" ? "ok" : (r.state === "rejected" ? "danger" : "warn");
            return '<span class="claire-tag ' + cls + '">' + esc(r.state) + "</span>";
          } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (r) {
            if (!r.contrib || r.approved || r.state === "withdrawn") return "—";
            return '<button type="button" class="claire-btn claire-btn-sm" data-withdraw-kb="' + esc(r.kb) + '" data-withdraw-c="' + esc(r.contrib) + '">' + esc(t("console.js.kb.withdraw_btn")) + "</button>";
          } }
        ],
        rows: rows
      });
      h += "</div>";
      return h;
    }

    function buildRuleTab() {
      var rd = state.ruleData || { server_switch: false, rule: { state: "paused", memberships: false, mutual: false, include_uploads: false, themes: [] } };
      var rule = rd.rule || {};
      var active = rule.state === "active";
      var themeNums = (rule.themes || []).map(function (x) { return typeof x === "object" ? x.number : x; }).filter(Boolean).join(", ");

      var h = "";
      if (!rd.server_switch) {
        h += '<div class="claire-note warn"><span>' + esc(t("console.js.kb.rule_server_off")) + "</span></div>";
      }
      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("bolt") + " " + esc(t("console.js.kb.rule_card")) + ' <span class="claire-tag ' + (active ? "ok" : "warn") + '">' + esc(active ? t("console.js.kb.rule_active_badge") : t("console.js.kb.rule_paused_badge")) + '</span></h3>' +
        '<div class="claire-actions">' +
        '<button type="button" class="claire-btn sec claire-btn-sm" id="kb-rule-dry">' + esc(t("console.js.kb.rule_dry_run")) + "</button>" +
        (active
          ? '<button type="button" class="claire-btn danger claire-btn-sm" id="kb-rule-pause">' + esc(t("console.js.kb.rule_pause")) + "</button>"
          : '<button type="button" class="claire-btn pri claire-btn-sm" id="kb-rule-activate">' + esc(t("console.js.kb.rule_activate")) + "</button>") +
        "</div></div>";
      h += '<div class="claire-srow"><div class="claire-srow-info"><label for="rule-memberships">' + esc(t("console.js.kb.rule_memberships")) + '</label></div><input id="rule-memberships" type="checkbox" class="claire-switch"' + (rule.memberships ? " checked" : "") + "/></div>";
      h += '<div class="claire-srow"><div class="claire-srow-info"><label for="rule-mutual">' + esc(t("console.js.kb.rule_mutual")) + '</label></div><input id="rule-mutual" type="checkbox" class="claire-switch"' + (rule.mutual ? " checked" : "") + "/></div>";
      h += '<div class="claire-srow"><div class="claire-srow-info"><label for="rule-uploads">' + esc(t("console.js.kb.rule_include_uploads")) + '</label></div><input id="rule-uploads" type="checkbox" class="claire-switch"' + (rule.include_uploads ? " checked" : "") + "/></div>";
      h += '<div class="claire-field"><label class="claire-field-label" for="rule-themes">' + esc(t("console.js.kb.rule_themes")) + '</label><input id="rule-themes" class="claire-input" value="' + esc(themeNums) + '"/></div>';
      h += '<div class="claire-actions"><button type="button" class="claire-btn pri" id="kb-rule-save">' + esc(t("console.js.kb.rule_save")) + "</button></div></div>";
      return h;
    }

    function buildRepTab() {
      var rep = state.rep || { level: "good", fiction_share: 0, fiction_like_documents: 0, approval_rate: 1, documents: [], pending_limit: 20 };
      var ficPct = Math.round(Number(rep.fiction_share || 0) * 100);
      var appPct = Math.round(Number(rep.approval_rate == null ? 1 : rep.approval_rate) * 100);

      var h = '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("chart") + " " + esc(t("console.js.kb.rep_card")) + ' <span class="claire-tag ' + (rep.level === "good" ? "ok" : (rep.level === "limited" ? "warn" : "danger")) + '">' + esc(rep.level || "good") + "</span></h3></div>";
      h += '<div class="claire-kpis">' +
        '<div class="claire-kpi"><strong>' + esc(ficPct) + '%</strong><span>' + esc(t("console.js.kb.rep_fiction_share")) + "</span></div>" +
        '<div class="claire-kpi"><strong>' + esc(appPct) + '%</strong><span>' + esc(t("console.js.kb.rep_approval_180")) + "</span></div>" +
        '<div class="claire-kpi"><strong>' + esc(rep.pending_limit || 20) + '</strong><span>' + esc(t("console.js.kb.rep_pending_limit")) + "</span></div>" +
        "</div></div>";

      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + esc(t("console.js.kb.rep_fiction_docs")) + "</h3></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.rep_fiction_docs"),
        columns: [
          { label: t("kb.dlg.col_docs"), render: function (d) { return esc(d.title || d.document); } },
          { label: t("kb.pub.nav_themes"), render: function (d) { return esc(d.theme_label || d.theme || "—"); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (d) { return '<button type="button" class="claire-btn claire-btn-sm" data-fic-contrib="' + esc(d.document) + '" data-fic-title="' + esc(d.title || "") + '" data-fic-theme="' + esc(d.theme || "") + '" data-fic-tlabel="' + esc(d.theme_label || "") + '">' + esc(t("ws.kb.contribute_to_theme")) + "</button>"; } }
        ],
        rows: rep.documents || []
      });
      h += "</div>";

      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + esc(t("console.js.kb.rep_effects_title")) + "</h3></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.rep_effects_title"),
        columns: [
          { label: "Level", key: "level" },
          { label: t("console.js.kb.rep_pending_limit"), key: "pending" },
          { label: "Auto-contribution", key: "auto" },
          { label: "Review priority", key: "prio" }
        ],
        rows: [
          { level: "good", pending: "20", auto: "Immediate", prio: "Standard" },
          { level: "limited", pending: "5", auto: "Manual only", prio: "Lower queue priority" },
          { level: "low", pending: "1", auto: "Disabled", prio: "Strict review required" }
        ]
      });
      h += "</div>";
      return h;
    }

    function buildMemTab() {
      var joined = state.kbs.filter(function (k) { return !k.is_personal && k.role; });
      var h = '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("globe") + " " + esc(t("console.js.kb.mem_card")) + "</h3></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.mem_card"),
        columns: [
          { label: t("kb.dlg.col_kb"), render: function (r) { return '<a href="/kb/' + esc(r.number) + '/">' + esc(r.label) + "</a>"; } },
          { label: t("kb.pub.col_origin"), render: function (r) { return '<span class="claire-tag ' + (r.origin ? "rem" : "acc") + '">' + esc(r.origin ? t("kb.pub.origin_remote") : t("kb.pub.origin_local")) + "</span>"; } },
          { label: t("kb.pub.col_role"), render: function (r) { return esc(r.role) + (r.grant_expires_at ? " (grant)" : ""); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (r) { return '<button type="button" class="claire-btn claire-btn-sm" data-leave-kb="' + esc(r.number) + '">' + esc(t("kb.pub.kb_leave")) + "</button>"; } }
        ],
        rows: joined
      });
      h += "</div>";

      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("users") + " " + esc(t("console.js.kb.mutual_card")) + "</h3></div>";
      h += '<form id="kb-mutual-form" style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">' +
        '<div class="claire-field" style="flex:1;min-width:220px"><label class="claire-field-label" for="kb-mutual-acc">' + esc(t("console.js.kb.mutual_request_label")) + '</label><input id="kb-mutual-acc" class="claire-input" required/></div>' +
        '<button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.mutual_request_btn")) + "</button></form>";
      h += ui().renderTable({
        caption: t("console.js.kb.mutual_card"),
        columns: [
          { label: "Account", render: function (m) { return esc(m.display_name || m.account); } },
          { label: "State", render: function (m) { return '<span class="claire-tag ' + (m.state === "active" ? "ok" : "warn") + '">' + esc(m.state) + "</span>"; } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (m) {
            var btns = "";
            if (m.state === "pending" && !m.requested_by_me) {
              btns += '<button type="button" class="claire-btn pri claire-btn-sm" data-mut-accept="' + esc(m.account) + '">' + esc(t("console.js.kb.mutual_accept")) + "</button> ";
            }
            btns += '<button type="button" class="claire-btn claire-btn-sm" data-mut-end="' + esc(m.account) + '">' + esc(t("console.js.kb.mutual_end")) + "</button>";
            return btns;
          } }
        ],
        rows: state.mutual
      });
      h += "</div>";
      return h;
    }

    function collectRuleDraft(newState) {
      var themes = (host.querySelector("#rule-themes").value || "").split(",").map(function (s) { return s.trim(); }).filter(Boolean);
      return {
        state: newState || (state.ruleData && state.ruleData.rule && state.ruleData.rule.state) || "paused",
        memberships: !!host.querySelector("#rule-memberships").checked,
        mutual: !!host.querySelector("#rule-mutual").checked,
        include_uploads: !!host.querySelector("#rule-uploads").checked,
        themes: themes
      };
    }

    function draw(results) {
      var kbUnavailable = !!(results && results[0] && results[0].unavailable);
      host.innerHTML = ui().renderTabs({
        id: "settings-kb-tabs",
        ariaLabel: t("console.section.settings.kb.title"),
        active: activeTab,
        tabs: [
          { id: "personal", label: t("console.js.kb.tab_personal"), icon: "folder", html: buildPersonalTab(kbUnavailable) },
          { id: "subs", label: t("console.js.kb.tab_subs"), icon: "merge", html: buildSubsTab() },
          { id: "rule", label: t("console.js.kb.tab_rule"), icon: "bolt", html: buildRuleTab() },
          { id: "rep", label: t("console.js.kb.tab_rep"), icon: "chart", html: buildRepTab() },
          { id: "mem", label: t("console.js.kb.tab_mem"), icon: "users", html: buildMemTab() }
        ]
      });
      ui().enhance(host);

      var tabRoot = host.querySelector("#settings-kb-tabs");
      if (tabRoot) {
        tabRoot.addEventListener("claire:tabchange", function (ev) {
          activeTab = ev.detail.tab;
        });
      }

      var createBtn = host.querySelector("#kb-create-btn");
      if (createBtn && dlgs()) {
        createBtn.addEventListener("click", function () {
          dlgs().openCreateKbDialog({ onCreated: function () { loadAll(); } });
        });
      }

      var pubToggle = host.querySelector("[data-personal-public]");
      if (pubToggle) {
        pubToggle.addEventListener("change", function () {
          var num = pubToggle.getAttribute("data-personal-public");
          var next = pubToggle.checked;
          var apply = function () {
            ui().request("/kb/" + encodeURIComponent(num) + "/", { method: "PATCH", body: { is_public: next } }).then(function (res) {
              if (!res.ok) { pubToggle.checked = !next; ui().toast(res.error || "Failed", "danger"); return; }
              ui().toast(t("common.action.save", null, "Saved"), "ok");
              loadAll();
            });
          };
          if (next) {
            ui().openDialog({
              title: t("console.js.kb.public_confirm_title"),
              icon: "globe",
              bodyHtml: "<p>" + esc(t("console.js.kb.public_confirm_body")) + "</p>",
              onSubmit: function () { apply(); return true; }
            }).then(function (rv) {
              if (rv !== "confirm") pubToggle.checked = false;
            });
          } else {
            apply();
          }
        });
      }

      host.querySelectorAll("[data-withdraw-kb]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var kb = btn.getAttribute("data-withdraw-kb");
          var cid = btn.getAttribute("data-withdraw-c");
          btn.disabled = true;
          ui().request("/kb/" + encodeURIComponent(kb) + "/contributions/" + encodeURIComponent(cid), { method: "DELETE" }).then(function (res) {
            if (!res.ok) { btn.disabled = false; ui().toast(res.error || "Failed", "danger"); return; }
            ui().toast(t("console.js.kb.withdraw_done"), "ok");
            loadAll();
          });
        });
      });

      var dryBtn = host.querySelector("#kb-rule-dry");
      if (dryBtn && dlgs()) dryBtn.addEventListener("click", function () { dlgs().openAutoRuleDryRunDialog({ payload: collectRuleDraft() }); });
      var pauseBtn = host.querySelector("#kb-rule-pause");
      if (pauseBtn) {
        pauseBtn.addEventListener("click", function () {
          ui().request("/api/auto-rule", { method: "PUT", body: collectRuleDraft("paused") }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            loadAll();
          });
        });
      }
      var actBtn = host.querySelector("#kb-rule-activate");
      if (actBtn) {
        actBtn.addEventListener("click", function () {
          ui().request("/api/auto-rule", { method: "PUT", body: collectRuleDraft("active") }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            loadAll();
          });
        });
      }
      var saveRuleBtn = host.querySelector("#kb-rule-save");
      if (saveRuleBtn) {
        saveRuleBtn.addEventListener("click", function () {
          ui().request("/api/auto-rule", { method: "PUT", body: collectRuleDraft() }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            ui().toast(t("common.action.save"), "ok");
            loadAll();
          });
        });
      }

      host.querySelectorAll("[data-fic-contrib]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          if (!dlgs()) return;
          dlgs().openContribPreview({
            documentId: btn.getAttribute("data-fic-contrib"),
            documentTitle: btn.getAttribute("data-fic-title"),
            themeId: btn.getAttribute("data-fic-theme"),
            themeLabel: btn.getAttribute("data-fic-tlabel"),
            onSubmitted: function () { loadAll(); }
          });
        });
      });

      host.querySelectorAll("[data-leave-kb]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var kb = btn.getAttribute("data-leave-kb");
          ui().request("/kb/" + encodeURIComponent(kb) + "/membership", { method: "DELETE" }).then(function (res) {
            if (!res.ok) { ui().toast(res.error || "Failed", "danger"); return; }
            loadAll();
          });
        });
      });

      var mutForm = host.querySelector("#kb-mutual-form");
      if (mutForm) {
        mutForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var val = host.querySelector("#kb-mutual-acc").value.trim();
          if (!val) return;
          ui().request("/api/mutual-collaborations", { method: "POST", body: { account: val } }).then(function (res) {
            if (!res.ok) { ui().toast(res.error || "Failed", "danger"); return; }
            loadAll();
          });
        });
      }
      host.querySelectorAll("[data-mut-accept]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          ui().request("/api/mutual-collaborations/" + encodeURIComponent(btn.getAttribute("data-mut-accept")), { method: "PUT", body: { action: "accept" } }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            loadAll();
          });
        });
      });
      host.querySelectorAll("[data-mut-end]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          ui().request("/api/mutual-collaborations/" + encodeURIComponent(btn.getAttribute("data-mut-end")), { method: "DELETE" }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            loadAll();
          });
        });
      });
    }

    loadAll();
  }

  function renderSettingsKbManage(host, bs) {
    var currentNum = (bs && bs.kb_number) ? String(bs.kb_number) : "";
    var activeTab = "queue";
    var selectedPendingId = null;
    var selectedSourceKb = "";

    function load() {
      ui().request("/kb?page=true&limit=50").then(function (res) {
        var all = (res.ok && res.data && (res.data.orderedItems || res.data.items)) || [];
        var managed = all.filter(function (k) { return !k.is_personal && (k.role === "moderator" || k.role === "collaborator"); });
        if (!currentNum && managed.length) currentNum = String(managed[0].number);
        if (!currentNum) {
          host.innerHTML = '<div class="claire-card"><p class="claire-hint">' + esc(res.unavailable ? t("kb.dlg.backend_pending") : t("console.js.kb.no_managed_kbs")) + "</p>" +
            '<div class="claire-actions"><a class="claire-btn" href="/kb">' + esc(t("kb.pub.nav_kbs")) + '</a><a class="claire-btn sec" href="/settings/kb">' + esc(t("kb.pub.nav_settings")) + "</a></div></div>";
          return;
        }
        Promise.all([
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/"),
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/contributions"),
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/members"),
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/moderation-log")
        ]).then(function (sub) {
          drawManage(managed, all, sub[0].data || managed.find(function (k) { return String(k.number) === currentNum; }) || { number: currentNum }, sub[1].data || {}, sub[2].data || {}, sub[3].data || {});
        });
      });
    }

    function drawManage(managed, allKbs, kb, contribData, memData, logData) {
      var role = kb.role || "collaborator";
      var isMod = role === "moderator";
      var contribs = contribData.contributions || contribData.orderedItems || [];
      var pending = contribs.filter(function (c) { return c.state === "pending"; }).sort(function (a, b) {
        var ra = (a.contributor_reputation && a.contributor_reputation.approval_rate) || 0.5;
        var rb = (b.contributor_reputation && b.contributor_reputation.approval_rate) || 0.5;
        if (rb !== ra) return rb - ra;
        return String(a.contributed_at || "").localeCompare(String(b.contributed_at || ""));
      });
      if (!selectedPendingId && pending.length) selectedPendingId = String(pending[0].number);
      var nonPending = contribs.filter(function (c) { return c.state !== "pending"; });
      var members = memData.members || memData.orderedItems || [];
      var logs = logData.logs || logData.orderedItems || [];

      var switchHtml = '<div class="claire-card"><div class="claire-card-head">' +
        '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
        '<label for="rkb" style="font-weight:600;font-size:13px">' + esc(t("console.js.kb.switch_kb")) + "</label>" +
        '<select id="rkb" class="claire-select" style="width:auto;min-width:220px">' +
        (managed.length ? managed : [kb]).map(function (k) {
          return '<option value="' + esc(k.number) + '"' + (String(k.number) === currentNum ? " selected" : "") + ">" + esc(k.label || ("#" + k.number)) + " (#" + esc(k.number) + ")</option>";
        }).join("") + "</select>" +
        '<span class="claire-tag ' + (isMod ? "ok" : "acc") + '">' + esc(role) + "</span>" +
        '</div><a class="claire-btn claire-btn-sm" href="/kb/' + esc(currentNum) + '/">' + esc(t("kb.pub.open_origin", null, "Open KB view")) + "</a></div></div>";

      var selPending = pending.find(function (c) { return String(c.number) === String(selectedPendingId); }) || pending[0] || null;
      var queueHtml = '<div class="claire-split"><div class="claire-list-col">' +
        (pending.length ? pending.map(function (c) {
          var isCur = selPending && String(c.number) === String(selPending.number);
          var repLvl = (c.contributor_reputation && c.contributor_reputation.level) || "good";
          return '<button type="button" class="claire-list-item' + (isCur ? " active" : "") + '" data-pick-pending="' + esc(c.number) + '">' +
            '<div class="claire-list-item-row"><strong>' + esc(c.title || c.document) + '</strong><span class="claire-tag ' + (repLvl === "good" ? "ok" : "warn") + '">' + esc(repLvl) + "</span></div>" +
            '<div class="claire-list-item-sub"><span>' + esc((c.contributor && c.contributor.display_name) || "Member") + "</span>" + (c.origin === "remote" ? '<span class="claire-tag rem">Remote</span>' : "") + "</div></button>";
        }).join("") : '<div class="claire-card"><p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p></div>") +
        '</div><div class="claire-detail-col">';
      if (selPending) {
        var help = selPending.review_help || {};
        queueHtml += '<div class="claire-card"><div class="claire-card-head"><h3>' + esc(selPending.title || selPending.document) + '</h3>' +
          '<div class="claire-actions"><button type="button" class="claire-btn pri claire-btn-sm" data-q-act="approve" data-q-id="' + esc(selPending.number) + '">' + esc(t("console.js.kb.queue_approve")) + "</button>" +
          '<button type="button" class="claire-btn danger claire-btn-sm" data-q-act="reject" data-q-id="' + esc(selPending.number) + '">' + esc(t("console.js.kb.queue_reject")) + "</button></div></div>";
        if (selPending.remote_deleted) {
          queueHtml += '<div class="claire-note warn"><span>' + esc(t("kb.pub.doc_remote_deleted")) + "</span></div>";
        }
        queueHtml += '<p class="claire-hint">Contributor: ' + esc((selPending.contributor && selPending.contributor.display_name) || "—") +
          " · Route: " + esc(help.routed_by || "manual") +
          (help.theme_score != null ? " (score " + esc(Number(help.theme_score).toFixed(2)) + ")" : "") + "</p>";
        if (Array.isArray(help.other_decisions) && help.other_decisions.length) {
          queueHtml += '<div><strong style="font-size:12.5px">' + esc(t("console.js.kb.queue_other_decisions")) + "</strong>" +
            ui().renderTable({
              caption: t("console.js.kb.queue_other_decisions"),
              columns: [
                { label: t("kb.dlg.col_kb"), render: function (d) { return esc(d.label || ("#" + d.knowledge_base)); } },
                { label: "Decision", render: function (d) { return '<span class="claire-tag">' + esc(d.decision) + "</span>"; } },
                { label: "Reason", render: function (d) { return esc(d.reason || "—"); } }
              ],
              rows: help.other_decisions
            }) + "</div>";
        }
        queueHtml += "</div>";
      } else {
        queueHtml += '<div class="claire-card"><p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p></div>";
      }
      queueHtml += "</div></div>";

      var contentHtml = '<div class="claire-card">' + ui().renderTable({
        caption: t("console.js.kb.tab_content"),
        columns: [
          { label: t("kb.dlg.col_docs"), render: function (c) { return "<strong>" + esc(c.title || c.document) + "</strong>" + (c.origin === "remote" ? ' <span class="claire-tag rem">Remote</span>' : ""); } },
          { label: "Contributor", render: function (c) { return esc((c.contributor && c.contributor.display_name) || "—"); } },
          { label: "State", render: function (c) { return '<span class="claire-tag ' + (c.state === "approved" ? "ok" : "warn") + '">' + esc(c.state) + "</span>" + (c.updates_stopped ? ' <span class="claire-tag warn">Frozen</span>' : ""); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (c) {
            return ui().renderRowMenu([
              { action: c.state === "hidden" ? "unhide" : "hide", value: c.number, label: t(c.state === "hidden" ? "console.js.kb.content_unhide" : "console.js.kb.content_hide"), icon: "eye" },
              { action: c.updates_stopped ? "restart_updates" : "stop_updates", value: c.number, label: t(c.updates_stopped ? "console.js.kb.content_unfreeze" : "console.js.kb.content_freeze"), icon: "lock" },
              { sep: true },
              { action: "remove", value: c.number, label: t("console.js.kb.content_remove"), icon: "trash", danger: true }
            ], c.title || c.document);
          } }
        ],
        rows: nonPending
      }) + "</div>";

      var sourceChoices = allKbs.filter(function (k) { return String(k.number) !== currentNum; });
      if (!selectedSourceKb && sourceChoices.length) selectedSourceKb = String(sourceChoices[0].number);
      var curateHtml = '<div class="claire-split"><div class="claire-list-col">' +
        '<div class="claire-card"><strong style="font-size:13px">' + esc(t("console.js.kb.curate_source")) + "</strong>" +
        sourceChoices.map(function (sKb) {
          return '<button type="button" class="claire-list-item' + (String(sKb.number) === selectedSourceKb ? " active" : "") + '" data-curate-src="' + esc(sKb.number) + '">' +
            '<strong>' + esc(sKb.label) + '</strong><span class="claire-list-item-sub">' + esc(sKb.is_personal ? "Personal" : "Shared") + " · " + esc((sKb.stats && sKb.stats.documents) || 0) + " docs</span></button>";
        }).join("") +
        "</div></div><div class=\"claire-detail-col\"><div class=\"claire-card\" id=\"kb-curate-docs\"><p class=\"claire-hint\">" + esc(t("kb.ui.loading")) + "</p></div></div></div>";

      var tabs = [
        { id: "queue", label: t("console.js.kb.tab_queue"), badge: pending.length, icon: "inbox", html: queueHtml },
        { id: "content", label: t("console.js.kb.tab_content"), icon: "doc", html: contentHtml },
        { id: "curate", label: t("console.js.kb.tab_curate"), icon: "puzzle", html: curateHtml }
      ];

      if (isMod) {
        var peopleHtml = '<div class="claire-card">' + ui().renderTable({
          caption: t("console.js.kb.tab_people"),
          columns: [
            { label: "Member", render: function (m) { return "<strong>" + esc(m.display_name || m.account) + "</strong>"; } },
            { label: t("kb.pub.col_role"), render: function (m) { return '<span class="claire-tag acc">' + esc(m.role) + "</span>"; } },
            { label: "State", render: function (m) { return esc(m.state || "active"); } },
            { label: t("kb.pub.col_actions"), actions: true, render: function (m) {
              var items = [];
              if (m.state === "pending") items.push({ action: "mem_approve", value: m.account, label: t("console.js.kb.people_approve"), icon: "check" });
              if (m.role !== "collaborator" && m.role !== "moderator") items.push({ action: "mem_collab", value: m.account, label: t("console.js.kb.people_grant_collab"), icon: "user" });
              if (m.role === "collaborator") items.push({ action: "mem_member", value: m.account, label: t("console.js.kb.people_revoke_collab"), icon: "user" });
              items.push({ sep: true });
              items.push({ action: "mem_ban", value: m.account, label: t("console.js.kb.people_ban"), icon: "trash", danger: true });
              return ui().renderRowMenu(items, m.display_name || m.account);
            } }
          ],
          rows: members
        }) + '</div><form class="claire-card" id="kb-remote-grant-form"><div class="claire-field"><label class="claire-field-label" for="kb-remote-handle">' + esc(t("console.js.kb.remote_grant_label")) + '</label><input id="kb-remote-handle" class="claire-input" placeholder="@alice@example.org" required/></div><div class="claire-actions"><button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.remote_grant_btn")) + "</button></div></form>";

        var seedsVal = Array.isArray(kb.seed_entities) ? kb.seed_entities.join(", ") : "";
        var settingsHtml = '<form class="claire-card" id="kb-manage-settings-form">' +
          '<div class="claire-field"><label class="claire-field-label" for="mset-label">' + esc(t("kb.dlg.field_name")) + '</label><input id="mset-label" class="claire-input" value="' + esc(kb.label || "") + '" required/></div>' +
          '<div class="claire-field"><label class="claire-field-label" for="mset-desc">' + esc(t("kb.dlg.field_desc")) + '</label><textarea id="mset-desc" class="claire-textarea">' + esc(kb.description || "") + "</textarea></div>" +
          '<div class="claire-field"><label class="claire-field-label" for="mset-seeds">' + esc(t("kb.dlg.field_seeds")) + '</label><input id="mset-seeds" class="claire-input" value="' + esc(seedsVal) + '" data-validate data-error-id="mset-seeds-err"/><span id="mset-seeds-err" class="claire-error" hidden></span></div>' +
          '<div class="claire-field"><label class="claire-field-label" for="mset-policy">' + esc(t("kb.pub.col_policy")) + '</label><select id="mset-policy" class="claire-select">' +
          '<option value="open"' + (kb.join_policy === "open" ? " selected" : "") + ">" + esc(t("console.js.kb.settings_join_open")) + "</option>" +
          '<option value="approval"' + (kb.join_policy === "approval" ? " selected" : "") + ">" + esc(t("console.js.kb.settings_join_approval")) + "</option>" +
          '<option value="invite"' + (kb.join_policy === "invite" ? " selected" : "") + ">" + esc(t("console.js.kb.settings_join_invite")) + "</option></select></div>" +
          '<div class="claire-actions"><button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.settings_save")) + "</button>" +
          '<button type="button" class="claire-btn sec" id="kb-archive-btn">' + esc(kb.state === "archived" ? t("console.js.kb.settings_unarchive") : t("console.js.kb.settings_archive")) + "</button></div></form>";

        var logHtml = '<div class="claire-card">' + ui().renderTable({
          caption: t("console.js.kb.tab_log"),
          columns: [
            { label: "Actor", render: function (l) { return esc(l.actor_display_name || l.actor || "—"); } },
            { label: "Action", render: function (l) { return '<span class="claire-tag">' + esc(l.action || "") + "</span>"; } },
            { label: "Target", render: function (l) { return esc(l.target || "—"); } },
            { label: "Reason", render: function (l) { return esc(l.reason || "—"); } },
            { label: "When", render: function (l) { return esc(l.created_at || ""); } }
          ],
          rows: logs
        }) + "</div>";

        tabs.push({ id: "people", label: t("console.js.kb.tab_people"), icon: "users", html: peopleHtml });
        tabs.push({ id: "settings", label: t("console.js.kb.tab_settings"), icon: "settings", html: settingsHtml });
        tabs.push({ id: "log", label: t("console.js.kb.tab_log"), icon: "list", html: logHtml });
      }

      host.innerHTML = switchHtml + ui().renderTabs({
        id: "kb-manage-tabs",
        ariaLabel: kb.label || currentNum,
        active: activeTab,
        tabs: tabs
      });
      ui().enhance(host);

      var tabRoot = host.querySelector("#kb-manage-tabs");
      if (tabRoot) tabRoot.addEventListener("claire:tabchange", function (ev) { activeTab = ev.detail.tab; });

      var switcher = host.querySelector("#rkb");
      if (switcher) {
        switcher.addEventListener("change", function () {
          currentNum = switcher.value;
          selectedPendingId = null;
          if (window.history && window.history.replaceState) {
            window.history.replaceState(null, "", "/settings/kb/" + encodeURIComponent(currentNum));
          }
          load();
        });
      }

      host.querySelectorAll("[data-pick-pending]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectedPendingId = btn.getAttribute("data-pick-pending");
          drawManage(managed, allKbs, kb, contribData, memData, logData);
        });
      });

      host.querySelectorAll("[data-q-act]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          var act = btn.getAttribute("data-q-act");
          var cid = btn.getAttribute("data-q-id");
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/contributions/" + encodeURIComponent(cid), {
            method: "PATCH",
            body: { action: act }
          }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            load();
          });
        });
      });

      host.addEventListener("click", function (ev) {
        var mi = ev.target && ev.target.closest ? ev.target.closest("[data-menu-action]") : null;
        if (!mi) return;
        var act = mi.getAttribute("data-menu-action");
        var val = mi.getAttribute("data-menu-value");
        if (act === "remove" && dlgs()) {
          dlgs().openRemoveContributionDialog({ kbNumber: currentNum, contribNumber: val, onRemoved: function () { load(); } });
        } else if (act === "hide" || act === "unhide" || act === "stop_updates" || act === "restart_updates") {
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/contributions/" + encodeURIComponent(val), { method: "PATCH", body: { action: act } }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            load();
          });
        } else if (act === "mem_approve" || act === "mem_ban") {
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/members/" + encodeURIComponent(val), { method: "PATCH", body: { action: act === "mem_approve" ? "approve" : "ban" } }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            load();
          });
        } else if (act === "mem_collab" || act === "mem_member") {
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/members/" + encodeURIComponent(val), { method: "PATCH", body: { role: act === "mem_collab" ? "collaborator" : "member" } }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            load();
          });
        }
      });

      host.querySelectorAll("[data-curate-src]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          selectedSourceKb = btn.getAttribute("data-curate-src");
          loadCurateSource();
        });
      });

      function loadCurateSource() {
        var box = host.querySelector("#kb-curate-docs");
        if (!box || !selectedSourceKb) return;
        var existingDocs = new Set(contribs.map(function (c) { return String(c.document); }));
        ui().request("/kb/" + encodeURIComponent(selectedSourceKb) + "/contributions").then(function (r) {
          var items = (r.ok && r.data && (r.data.contributions || r.data.orderedItems)) || [];
          var ch = '<form id="kb-curate-form" class="claire-panel-inner">';
          items.forEach(function (it) {
            var dup = existingDocs.has(String(it.document));
            ch += '<label class="claire-srow"><input type="checkbox" name="doc" value="' + esc(it.document) + '"' + (dup ? " disabled" : "") + '/>' +
              '<span class="claire-srow-info"><strong>' + esc(it.title || it.document) + '</strong><span>' + (dup ? esc(t("console.js.kb.curate_dup")) : esc(it.document)) + "</span></span></label>";
          });
          if (!items.length) ch += '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
          else ch += '<div class="claire-actions"><button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.curate_submit")) + "</button></div>";
          ch += "</form>";
          box.innerHTML = ch;
          var form = box.querySelector("#kb-curate-form");
          if (form) {
            form.addEventListener("submit", function (ev) {
              ev.preventDefault();
              var checked = Array.prototype.slice.call(form.querySelectorAll('input[name="doc"]:checked')).map(function (x) { return x.value; });
              if (!checked.length) return;
              ui().request("/kb/" + encodeURIComponent(currentNum) + "/curations", { method: "POST", body: { source: selectedSourceKb, documents: checked } }).then(function (cr) {
                if (!cr.ok) { ui().toast(cr.error || "Failed", "danger"); return; }
                ui().toast(t("kb.dlg.contrib_done"), "ok");
                load();
              });
            });
          }
        });
      }
      loadCurateSource();

      var rgForm = host.querySelector("#kb-remote-grant-form");
      if (rgForm) {
        rgForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var handle = host.querySelector("#kb-remote-handle").value.trim();
          if (!handle) return;
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/members/" + encodeURIComponent(handle), { method: "PATCH", body: { role: "collaborator" } }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            load();
          });
        });
      }

      var mForm = host.querySelector("#kb-manage-settings-form");
      if (mForm) {
        mForm.addEventListener("submit", function (ev) {
          ev.preventDefault();
          var seeds = host.querySelector("#mset-seeds").value.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
          if (seeds.length > 20) {
            host.querySelector("#mset-seeds").setCustomValidity(t("kb.dlg.err_seeds_max"));
            mForm.reportValidity();
            return;
          }
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/", {
            method: "PATCH",
            body: {
              label: host.querySelector("#mset-label").value.trim(),
              description: host.querySelector("#mset-desc").value.trim(),
              seed_entities: seeds,
              join_policy: host.querySelector("#mset-policy").value
            }
          }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            ui().toast(t("common.action.save"), "ok");
            load();
          });
        });
      }

      var archBtn = host.querySelector("#kb-archive-btn");
      if (archBtn) {
        archBtn.addEventListener("click", function () {
          var nextState = kb.state === "archived" ? "active" : "archived";
          ui().request("/kb/" + encodeURIComponent(currentNum) + "/", { method: "PATCH", body: { state: nextState } }).then(function (r) {
            if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
            load();
          });
        });
      }
    }

    load();
  }

  function parseBootstrap() {
    var el = document.getElementById("console-bootstrap");
    if (!el || !el.textContent) return {};
    try { return JSON.parse(el.textContent); } catch (_) { return {}; }
  }

  function boot() {
    var bs = parseBootstrap();
    var kbHost = document.getElementById("settings-kb-host");
    if (kbHost && bs.section === "kb" && bs.area === "settings") {
      renderSettingsKb(kbHost, bs);
    }
    var mgrHost = document.getElementById("settings-kb-manage-host");
    if (mgrHost && bs.section === "kb_manage") {
      renderSettingsKbManage(mgrHost, bs);
    }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window);
