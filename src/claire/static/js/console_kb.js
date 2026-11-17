/* Console pages /settings/kb and /settings/kb/{number} (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10.3). Each request goes to a route of docs/implementation/API_HTTP_CONTRACT.md. */
(function (root) {
  "use strict";

  function ui() { return root.ClaireUi; }
  function dlgs() { return root.ClaireKbDialogs; }
  function t(key, params) { return ui().t(key, params); }
  function esc(v) { return ui().esc(v); }
  function fail(res) { ui().toast(ui().errorText(res), "danger"); }
  function kbUrl(number, route) { return "/kb/" + encodeURIComponent(number) + "/" + (route || ""); }
  function roleLabel(role) { return role ? t("kb.role." + role) : "—"; }
  function levelTag(level) { return '<span class="claire-tag ' + (level === "good" ? "ok" : (level === "limited" ? "warn" : "danger")) + '">' + esc(t("kb.level." + level)) + "</span>"; }
  function percent(value) { return value == null ? "—" : Math.round(Number(value) * 100) + "%"; }
  function stateTag(state) { return '<span class="claire-tag ' + (state === "visible" || state === "accepted" ? "ok" : (state === "rejected" || state === "removed" || state === "failed" ? "danger" : "warn")) + '">' + esc(dlgs().stateLabel(state)) + "</span>"; }
  // The reason of a decision or of a log entry: the catalog text of a reason code, else the text as it is.
  function reasonText(reason) {
    var code = String(reason || "");
    if (!code) return "";
    var key = "kb.reason." + code;
    var text = t(key);
    if (text !== key) return text;
    // A log entry of a member who left has the role name as its reason.
    var roleKey = "kb.role." + code;
    var roleText = t(roleKey);
    return roleText === roleKey ? code : roleText;
  }
  // A shared knowledge base of another server (federation design Section 12): its label with a link to its page on that server, and the tag of the server.
  function remoteKbHtml(label, url, server) {
    var name = /^https?:\/\//.test(String(url || "")) ? '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(label) + "</a>" : esc(label);
    return name + ' <span class="claire-tag">' + esc(t("console.js.kb.curate_remote_tag", { server: server })) + "</span>";
  }
  function dateText(seconds) { return seconds ? new Date(Number(seconds) * 1000).toLocaleString(document.documentElement.lang || undefined) : "—"; }
  // The state of a grant of the role collaborator across servers: active, ended (the end time is past), or revoked.
  function grantStateTag(state) { return '<span class="claire-tag ' + (state === "active" ? "ok" : (state === "revoked" ? "danger" : "warn")) + '">' + esc(t("console.js.kb.grant_state." + state)) + "</span>"; }
  // The contributor withdraws a contribution while it is not approved and not closed (design Section 9.4; DELETE /kb/{number}/contributions/{contribution}).
  function canWithdraw(c) { return !!c && !c.approved && c.state !== "rejected" && c.state !== "removed"; }

  function renderSettingsKb(host, bs) {
    var activeTab = "personal";
    var account = (bs && bs.account) || {};
    // The path of the personal knowledge base of the account: "/@user/" in network mode, "/" in single mode (design Section 7).
    var homeHref = account.username ? "/@" + encodeURIComponent(account.username) + "/" : "/";
    var state = { personal: null, joined: [], memberships: [], subs: [], rule: null, serverSwitch: false, rep: null, mutual: [], autos: [], mine: [], blocks: [], themes: [], grants: [] };

    function loadAll() {
      return Promise.all([
        // GET /kb: each shared knowledge base has "role" and "membership_state" of the requester. A join request ("pending") and a ban ("banned") have no role, thus the list is not "?joined=true".
        ui().request("/kb"),
        ui().request("/submissions"),
        ui().request("/api/auto-rule"),
        ui().request("/api/reputation"),
        ui().request("/api/mutual-collaborations"),
        ui().request("/api/contributions?origin=auto"),
        ui().request("/api/contributions"),
        ui().request("/themes"),
        // GET /api/contribution-blocks: the identifiers of the documents with the flag "never contribute". The list of GET /documents has pages, thus it is not the source of this list.
        ui().request("/api/contribution-blocks"),
        // GET /api/kb/remote-grants: the grants of the role collaborator that the account got from shared knowledge bases of other servers (federation design Section 12.2).
        ui().request("/api/kb/remote-grants")
      ]).then(function (all) {
        var data = function (i) { return (all[i].ok && all[i].data) || {}; };
        state.personal = data(0).personal_kb || null;
        state.memberships = (data(0).knowledge_bases || []).filter(function (k) { return !!k.membership_state; });
        state.joined = state.memberships.filter(function (k) { return k.membership_state === "active"; });
        state.subs = data(1).submissions || [];
        state.rule = data(2).rule || null;
        state.serverSwitch = !!data(2).server_switch;
        state.rep = all[3].ok ? all[3].data : null;
        state.mutual = data(4).mutual_collaborations || [];
        state.autos = data(5).contributions || [];
        state.mine = data(6).contributions || [];
        state.themes = (data(7).themes || []).filter(function (th) { return th.state === "active"; });
        state.blocks = data(8).documents || [];
        state.grants = data(9).grants || [];
        draw(all[0].ok ? null : all[0]);
      });
    }

    function kbLink(c) { return c.knowledge_base ? '<a href="' + esc(kbUrl(c.knowledge_base)) + '">' + esc(c.label || c.knowledge_base) + "</a>" : "—"; }
    function withdrawBtn(kb, contribution) { return '<button type="button" class="claire-btn claire-btn-sm" data-withdraw-kb="' + esc(kb) + '" data-withdraw-c="' + esc(contribution) + '">' + esc(t("console.js.kb.withdraw_btn")) + "</button>"; }

    function buildPersonalTab(loadError) {
      var pkb = state.personal;
      var stats = (pkb && pkb.stats) || {};
      var reviewed = state.joined.filter(function (k) { return k.role === "moderator" || k.role === "collaborator"; });
      var h = "";
      if (loadError) h += '<div class="claire-note warn"><span>' + esc(ui().errorText(loadError)) + "</span></div>";
      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("folder") + " " + esc(t("console.js.kb.personal_card")) + "</h3>" +
        '<a class="claire-btn claire-btn-sm" href="' + esc(homeHref) + '">' + esc(t("console.js.kb.open_workspace")) + "</a></div>";
      if (pkb) {
        h += '<div class="claire-srow"><div class="claire-srow-info"><strong>' + esc(pkb.label) + ' <span class="claire-tag">' + esc(pkb.number) + "</span></strong>" +
          "<span>" + esc(t("kb.dlg.col_docs")) + ": " + esc(stats.documents || 0) + " · " + esc(t("kb.dlg.col_entities")) + ": " + esc(stats.entities || 0) + "</span></div></div>";
        h += '<div class="claire-srow"><div class="claire-srow-info"><label for="kb-personal-public">' + esc(t("console.js.kb.public_toggle")) + "</label><span>" + esc(t("console.js.kb.public_hint")) + "</span></div>" +
          '<input id="kb-personal-public" type="checkbox" class="claire-switch" data-personal-public' + (pkb.is_public ? " checked" : "") + "/></div>";
      } else {
        h += '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
      }
      h += "</div>";
      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("users") + " " + esc(t("console.js.kb.moderated_card")) + "</h3>" +
        '<div class="claire-actions"><a class="claire-btn claire-btn-sm" href="/kb">' + esc(t("kb.pub.nav_kbs")) + "</a>" +
        '<button type="button" class="claire-btn pri claire-btn-sm" id="kb-create-btn">' + ui().icon("plus") + " " + esc(t("campaign.make_kb_link")) + "</button></div></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.moderated_card"),
        columns: [
          { label: t("kb.dlg.col_kb"), render: function (r) { return '<a href="' + esc(kbUrl(r.number)) + '"><strong>' + esc(r.label) + "</strong></a> " + dlgs().typeTag(r.knowledge_type) + (r.state === "archived" ? " " + stateTag("archived") : ""); } },
          { label: t("kb.pub.col_role"), render: function (r) { return '<span class="claire-tag ok">' + esc(roleLabel(r.role)) + "</span>"; } },
          { label: t("kb.dlg.col_docs"), render: function (r) { return esc((r.stats && r.stats.documents) || 0); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (r) { return '<a class="claire-btn claire-btn-sm" href="/settings/kb/' + esc(r.number) + '">' + esc(t("kb.pub.kb_review_manage")) + "</a>"; } }
        ],
        rows: reviewed
      });
      h += "</div>";
      return h;
    }

    function buildSubsTab() {
      var rows = [];
      state.subs.forEach(function (sub) {
        (sub.items || []).forEach(function (item) {
          var theme = item.code === "no_theme" ? t("kb.dlg.result_no_theme") : (item.theme_label || item.theme || "—");
          if (!(item.targets || []).length) rows.push({ doc: item.title || item.document, theme: theme, routed: item.routed_by, score: item.score });
          (item.targets || []).forEach(function (target) {
            // A target on another server (federation design Section 12.1): the state is the decision of that server; it has no contribution of this server, so it has no withdrawal.
            rows.push({ doc: item.title || item.document, theme: theme, routed: item.routed_by, score: item.score, kb: target.knowledge_base, kbLabel: target.label || target.knowledge_base || target.actor, state: target.state, reason: target.reason, approved: !!target.approved, contribution: target.contribution, remote: target.remote ? target : null });
          });
        });
      });
      var h = '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("merge") + " " + esc(t("console.js.kb.subs_card")) + "</h3>" +
        '<button type="button" class="claire-btn pri claire-btn-sm" id="kb-submit-btn">' + esc(t("kb.dlg.contrib_title")) + "</button></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.subs_card"),
        columns: [
          { label: t("kb.dlg.col_document"), render: function (r) { return "<strong>" + esc(r.doc) + "</strong>"; } },
          { label: t("kb.dlg.col_theme"), render: function (r) { return esc(r.theme) + (r.routed ? ' <span class="claire-hint">' + esc(t("kb.routed." + r.routed)) + (r.score != null ? " " + esc(Number(r.score).toFixed(2)) : "") + "</span>" : ""); } },
          { label: t("kb.dlg.col_kb"), render: function (r) {
            if (r.remote) return remoteKbHtml(r.kbLabel, r.remote.url, r.remote.server) + (r.remote.with_grant ? ' <span class="claire-tag ok">' + esc(t("console.js.kb.subs_with_grant")) + "</span>" : "");
            return r.kb ? '<a href="' + esc(kbUrl(r.kb)) + '">' + esc(r.kbLabel) + "</a>" : "—";
          } },
          { label: t("kb.dlg.col_state"), render: function (r) {
            if (!r.state) return "—";
            var delivery = r.remote && r.remote.delivery && r.remote.delivery !== "delivered" ? ' <span class="claire-hint">' + esc(t("console.js.kb.subs_delivery." + r.remote.delivery)) + "</span>" : "";
            return stateTag(r.state) + (r.reason ? " " + esc(ui().errorText({ error: r.reason })) : "") + delivery;
          } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (r) { return r.contribution && canWithdraw(r) ? withdrawBtn(r.kb, r.contribution) : "—"; } }
        ],
        rows: rows
      });
      h += "</div>";

      // GET /api/contributions: each contribution with the credit of the account, in all shared knowledge bases, the newest first (design Section 10.3).
      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("doc") + " " + esc(t("console.js.kb.mine_card")) + "</h3></div>";
      h += '<p class="claire-hint">' + esc(t("console.js.kb.mine_hint")) + "</p>";
      h += ui().renderTable({
        caption: t("console.js.kb.mine_card"),
        columns: [
          { label: t("kb.dlg.col_document"), render: function (c) { return "<strong>" + esc(c.title || c.document || "—") + '</strong> <span class="claire-hint">' + esc(t("kb.origin." + c.origin)) + "</span>"; } },
          { label: t("kb.dlg.col_kb"), render: kbLink },
          { label: t("kb.dlg.col_state"), render: function (c) { return stateTag(c.state) + (c.approved ? ' <span class="claire-tag ok">' + esc(t("console.js.kb.mine_approved")) + "</span>" : "") + (c.reason ? " " + esc(reasonText(c.reason)) : ""); } },
          { label: t("console.js.kb.mine_col_date"), render: function (c) { return esc(dateText(c.contributed_at)); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (c) { return c.knowledge_base && canWithdraw(c) ? withdrawBtn(c.knowledge_base, c.number) : "—"; } }
        ],
        rows: state.mine
      });
      h += "</div>";
      return h;
    }

    function buildRuleTab() {
      var rule = state.rule || { state: "paused", memberships: false, mutual: false, include_uploads: false, themes: [] };
      var active = rule.state === "active";
      var selected = {};
      (rule.themes || []).forEach(function (th) { selected[String(th.number)] = true; });
      var h = "";
      if (!state.serverSwitch) h += '<div class="claire-note warn"><span>' + esc(t("console.js.kb.rule_server_off")) + "</span></div>";
      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("bolt") + " " + esc(t("console.js.kb.rule_card")) + ' <span class="claire-tag ' + (active ? "ok" : "warn") + '">' + esc(t(active ? "console.js.kb.rule_active_badge" : "console.js.kb.rule_paused_badge")) + "</span></h3>" +
        '<div class="claire-actions">' +
        (active
          ? '<button type="button" class="claire-btn danger claire-btn-sm" id="kb-rule-pause">' + esc(t("console.js.kb.rule_pause")) + "</button>"
          : '<button type="button" class="claire-btn pri claire-btn-sm" id="kb-rule-activate">' + esc(t("console.js.kb.rule_activate")) + "</button>") +
        "</div></div>";
      h += '<p class="claire-hint">' + esc(t("console.js.kb.rule_hint")) + "</p>";
      h += '<div class="claire-srow"><div class="claire-srow-info"><label for="rule-memberships">' + esc(t("console.js.kb.rule_memberships")) + '</label></div><input id="rule-memberships" type="checkbox" class="claire-switch"' + (rule.memberships ? " checked" : "") + "/></div>";
      h += '<div class="claire-srow"><div class="claire-srow-info"><label for="rule-mutual">' + esc(t("console.js.kb.rule_mutual")) + '</label></div><input id="rule-mutual" type="checkbox" class="claire-switch"' + (rule.mutual ? " checked" : "") + "/></div>";
      h += '<div class="claire-srow"><div class="claire-srow-info"><label for="rule-uploads">' + esc(t("console.js.kb.rule_include_uploads")) + '</label></div><input id="rule-uploads" type="checkbox" class="claire-switch"' + (rule.include_uploads ? " checked" : "") + "/></div>";
      h += '<fieldset class="claire-field" style="border:0;padding:0;margin:0"><legend class="claire-field-label">' + esc(t("console.js.kb.rule_themes")) + "</legend>";
      h += state.themes.length ? '<div class="claire-chips">' + state.themes.map(function (th) {
        return '<label class="claire-chip"><input type="checkbox" name="rule-theme" value="' + esc(th.number) + '"' + (selected[String(th.number)] ? " checked" : "") + "/><span>" + esc(th.label) + "</span></label>";
      }).join("") + "</div>" : '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
      h += "</fieldset>";
      h += '<div class="claire-actions"><button type="button" class="claire-btn sec" id="kb-rule-dry">' + esc(t("console.js.kb.rule_dry_run")) + "</button>" +
        '<button type="button" class="claire-btn pri" id="kb-rule-save">' + esc(t("console.js.kb.rule_save")) + "</button></div></div>";

      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + esc(t("console.js.kb.auto_card")) + "</h3></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.auto_card"),
        columns: [
          { label: t("kb.dlg.col_document"), render: function (c) { return esc(c.title || c.document || "—"); } },
          { label: t("kb.dlg.col_kb"), render: kbLink },
          { label: t("kb.dlg.col_state"), render: function (c) { return stateTag(c.state); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (c) { return c.knowledge_base && canWithdraw(c) ? withdrawBtn(c.knowledge_base, c.number) : "—"; } }
        ],
        rows: state.autos
      });
      h += "</div>";

      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + esc(t("console.js.kb.blocks_card")) + "</h3></div>";
      h += '<p class="claire-hint">' + esc(t("console.js.kb.blocks_hint")) + "</p>";
      h += ui().renderTable({
        caption: t("console.js.kb.blocks_card"),
        columns: [
          { label: t("kb.dlg.col_document"), render: function (id) { return '<a href="' + esc(homeHref + "?doc=" + encodeURIComponent(id)) + '"><code class="pref-mono">' + esc(id) + "</code></a>"; } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (id) { return '<button type="button" class="claire-btn claire-btn-sm" data-unblock-doc="' + esc(id) + '">' + esc(t("ws.kb.allow_contribute")) + "</button>"; } }
        ],
        rows: state.blocks
      });
      h += "</div>";
      return h;
    }

    function buildRepTab() {
      var rep = state.rep;
      if (!rep) return '<div class="claire-card"><p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p></div>";
      var h = '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("chart") + " " + esc(t("console.js.kb.rep_card")) + " " + levelTag(rep.level) + "</h3></div>";
      h += '<p class="claire-hint">' + esc(t("console.js.kb.rep_level_" + rep.level)) + "</p>";
      h += '<div class="claire-kpis">' +
        '<div class="claire-kpi"><strong>' + esc(percent(rep.fiction_share)) + "</strong><span>" + esc(t("console.js.kb.rep_fiction_share")) + "</span></div>" +
        '<div class="claire-kpi"><strong>' + esc(rep.fiction_like_documents || 0) + " / " + esc(rep.visible_documents || 0) + "</strong><span>" + esc(t("console.js.kb.rep_fiction_count")) + "</span></div>" +
        '<div class="claire-kpi"><strong>' + esc(percent(rep.approval_rate)) + "</strong><span>" + esc(t("console.js.kb.rep_approval_180")) + "</span></div>" +
        '<div class="claire-kpi"><strong>' + esc(rep.decisions || 0) + "</strong><span>" + esc(t("console.js.kb.rep_decisions")) + "</span></div>" +
        "</div>";
      // "limits" of GET /api/reputation: "pending" is the number of pending contributions in one shared knowledge base; "auto_documents" is the number of documents of one run of the automatic contribution (null: no limit; 0: paused).
      var limits = rep.limits || null;
      if (limits) {
        var autoLimit = limits.auto_documents;
        h += '<div class="claire-srow"><div class="claire-srow-info"><strong>' + esc(t("console.js.kb.rep_limit_pending")) + "</strong><span>" + esc(t("console.js.kb.rep_limit_pending_hint")) + '</span></div><span class="claire-tag">' + esc(limits.pending == null ? t("console.js.kb.rep_limit_none") : limits.pending) + "</span></div>";
        h += '<div class="claire-srow"><div class="claire-srow-info"><strong>' + esc(t("console.js.kb.rep_limit_auto")) + "</strong><span>" + esc(t("console.js.kb.rep_limit_auto_hint")) + '</span></div><span class="claire-tag' + (Number(autoLimit) === 0 && autoLimit != null ? " danger" : "") + '">' + esc(autoLimit == null ? t("console.js.kb.rep_limit_none") : (Number(autoLimit) === 0 ? t("console.js.kb.rep_limit_auto_paused") : autoLimit)) + "</span></div>";
      }
      h += "</div>";
      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + esc(t("console.js.kb.rep_fiction_docs")) + "</h3></div>";
      h += '<p class="claire-hint">' + esc(t("campaign.standing")) + ' <a href="/themes?type=fictional">' + esc(t("campaign.fictional_themes_link")) + "</a></p>";
      h += ui().renderTable({
        caption: t("console.js.kb.rep_fiction_docs"),
        columns: [
          { label: t("kb.dlg.col_document"), render: function (d) { return esc(d.title || d.document); } },
          { label: t("kb.dlg.col_theme"), render: function (d) { return d.theme ? '<a href="/themes/' + esc(d.theme) + '/">' + esc(d.theme_label || d.theme) + "</a>" : "—"; } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (d) {
            return '<button type="button" class="claire-btn claire-btn-sm" data-fic-contrib="' + esc(d.document) + '" data-fic-title="' + esc(d.title || "") + '" data-fic-theme="' + esc(d.theme || "") + '" data-fic-tlabel="' + esc(d.theme_label || "") + '">' + esc(t(d.theme ? "campaign.contribute_action" : "ws.kb.contribute_this_doc")) + "</button>";
          } }
        ],
        rows: rep.documents || []
      });
      h += "</div>";
      return h;
    }

    function buildMemTab() {
      var h = '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("globe") + " " + esc(t("console.js.kb.mem_card")) + "</h3></div>";
      h += ui().renderTable({
        caption: t("console.js.kb.mem_card"),
        columns: [
          { label: t("kb.dlg.col_kb"), render: function (r) { return '<a href="' + esc(kbUrl(r.number)) + '">' + esc(r.label) + "</a> " + dlgs().typeTag(r.knowledge_type); } },
          { label: t("kb.pub.col_role"), render: function (r) {
            if (r.membership_state === "active") return esc(roleLabel(r.role));
            return '<span class="claire-tag ' + (r.membership_state === "banned" ? "danger" : "warn") + '">' + esc(t("kb.member_state." + r.membership_state)) + "</span>";
          } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (r) {
            // A banned account cannot leave and cannot join again (design Section 10.2).
            if (r.membership_state === "banned") return "—";
            return '<button type="button" class="claire-btn claire-btn-sm" data-leave-kb="' + esc(r.number) + '">' + esc(t(r.membership_state === "pending" ? "kb.pub.kb_withdraw_join" : "kb.pub.kb_leave")) + "</button>";
          } }
        ],
        rows: state.memberships
      });
      h += "</div>";

      // The role collaborator in shared knowledge bases of other servers (federation design Section 12.2). The other server decides each use; this list shows the state that this server knows.
      if (state.grants.length) {
        h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("globe") + " " + esc(t("console.js.kb.grants_card")) + "</h3></div>";
        h += '<p class="claire-hint">' + esc(t("console.js.kb.grants_hint")) + "</p>";
        h += ui().renderTable({
          caption: t("console.js.kb.grants_card"),
          columns: [
            { label: t("kb.dlg.col_kb"), render: function (g) { return remoteKbHtml(g.label || g.knowledge_base, g.url, g.server); } },
            { label: t("kb.dlg.col_state"), render: function (g) { return grantStateTag(g.state); } },
            { label: t("console.js.kb.grants_col_end"), render: function (g) { return esc(dateText(g.state === "revoked" ? g.revoked_at : g.end_time)); } }
          ],
          rows: state.grants
        });
        h += "</div>";
      }

      h += '<div class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("users") + " " + esc(t("console.js.kb.mutual_card")) + "</h3></div>";
      h += '<p class="claire-hint">' + esc(t("console.js.kb.mutual_hint", { number: account.public_id || "" })) + "</p>";
      h += '<form id="kb-mutual-form" style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">' +
        '<div class="claire-field" style="flex:1;min-width:220px"><label class="claire-field-label" for="kb-mutual-acc">' + esc(t("console.js.kb.mutual_request_label")) + '</label><input id="kb-mutual-acc" class="claire-input" inputmode="numeric" pattern="[0-9]+" required/></div>' +
        '<button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.mutual_request_btn")) + "</button></form>";
      h += ui().renderTable({
        caption: t("console.js.kb.mutual_card"),
        columns: [
          { label: t("kb.dlg.col_account"), render: function (m) { return esc(m.display_name || m.account) + ' <span class="claire-hint">' + esc(m.account) + "</span>"; } },
          { label: t("kb.dlg.col_state"), render: function (m) { return '<span class="claire-tag ' + (m.state === "active" ? "ok" : "warn") + '">' + esc(t(m.state === "active" ? "console.js.kb.mutual_active" : (m.requested_by_me ? "console.js.kb.mutual_sent" : "console.js.kb.mutual_received"))) + "</span>"; } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (m) {
            var btns = "";
            if (m.state === "pending" && !m.requested_by_me) btns += '<button type="button" class="claire-btn pri claire-btn-sm" data-mut-accept="' + esc(m.account) + '">' + esc(t("console.js.kb.mutual_accept")) + "</button> ";
            btns += '<button type="button" class="claire-btn claire-btn-sm" data-mut-end="' + esc(m.account) + '">' + esc(t(m.state === "active" ? "console.js.kb.mutual_end" : "console.js.kb.mutual_remove")) + "</button>";
            return btns;
          } }
        ],
        rows: state.mutual
      });
      h += "</div>";
      return h;
    }

    // The body of PUT /api/auto-rule and of the dry run: each field is required (there is no default for a rule that sends documents).
    function collectRule(newState) {
      return {
        state: newState || (state.rule && state.rule.state) || "paused",
        memberships: !!host.querySelector("#rule-memberships").checked,
        mutual: !!host.querySelector("#rule-mutual").checked,
        include_uploads: !!host.querySelector("#rule-uploads").checked,
        themes: Array.prototype.slice.call(host.querySelectorAll('input[name="rule-theme"]:checked')).map(function (x) { return x.value; })
      };
    }

    function saveRule(body) {
      return ui().request("/api/auto-rule", { method: "PUT", body: body }).then(function (r) {
        if (!r.ok) { fail(r); return; }
        ui().toast(t("console.js.kb.rule_saved", { count: (r.data && r.data.contributions) || 0 }), "ok");
        loadAll();
      });
    }

    function on(selector, eventName, handler) {
      host.querySelectorAll(selector).forEach(function (el) { el.addEventListener(eventName, function (ev) { handler(el, ev); }); });
    }

    function draw(loadError) {
      host.innerHTML = ui().renderTabs({
        id: "settings-kb-tabs",
        ariaLabel: t("console.js.kb.title"),
        active: activeTab,
        tabs: [
          { id: "personal", label: t("console.js.kb.tab_personal"), icon: "folder", html: buildPersonalTab(loadError) },
          { id: "subs", label: t("console.js.kb.tab_subs"), icon: "merge", html: buildSubsTab() },
          { id: "rule", label: t("console.js.kb.tab_rule"), icon: "bolt", html: buildRuleTab() },
          { id: "rep", label: t("console.js.kb.tab_rep"), icon: "chart", html: buildRepTab() },
          { id: "mem", label: t("console.js.kb.tab_mem"), icon: "users", html: buildMemTab() }
        ]
      });
      ui().enhance(host);
      on("#settings-kb-tabs", "claire:tabchange", function (_el, ev) { activeTab = ev.detail.tab; });

      on("#kb-create-btn", "click", function () { dlgs().openCreateKbDialog({ onCreated: function () { loadAll(); } }); });
      on("#kb-submit-btn", "click", function () { dlgs().openContribPreview({ homeHref: homeHref, onSubmitted: function () { loadAll(); } }); });

      on("[data-personal-public]", "change", function (toggle) {
        var next = toggle.checked;
        var apply = function () {
          // PATCH on the path of the personal knowledge base: the path selects the knowledge base, no body field does.
          ui().request(homeHref, { method: "PATCH", body: { is_public: next } }).then(function (res) {
            if (!res.ok) { toggle.checked = !next; fail(res); return; }
            ui().toast(t("console.js.kb.saved"), "ok");
            loadAll();
          });
        };
        if (!next) { apply(); return; }
        ui().openDialog({
          title: t("console.js.kb.public_confirm_title"),
          icon: "globe",
          bodyHtml: "<p>" + esc(t("console.js.kb.public_confirm_body")) + "</p>",
          onSubmit: function () { apply(); return true; }
        }).then(function (rv) { if (rv !== "confirm") toggle.checked = false; });
      });

      on("[data-withdraw-kb]", "click", function (btn) {
        btn.disabled = true;
        ui().request(kbUrl(btn.getAttribute("data-withdraw-kb"), "contributions/" + encodeURIComponent(btn.getAttribute("data-withdraw-c"))), { method: "DELETE" }).then(function (res) {
          if (!res.ok) { btn.disabled = false; fail(res); return; }
          ui().toast(t("console.js.kb.withdraw_done"), "ok");
          loadAll();
        });
      });

      // Design Section 9.9 rule 6: the person sees the dry run before the rule turns on.
      on("#kb-rule-activate", "click", function () {
        var body = collectRule("active");
        dlgs().openAutoRuleDryRunDialog({ payload: body, onActivate: function () { saveRule(body); } });
      });
      on("#kb-rule-dry", "click", function () { dlgs().openAutoRuleDryRunDialog({ payload: collectRule() }); });
      on("#kb-rule-pause", "click", function () { saveRule(collectRule("paused")); });
      on("#kb-rule-save", "click", function () {
        var body = collectRule();
        if (body.state === "active") dlgs().openAutoRuleDryRunDialog({ payload: body, onActivate: function () { saveRule(body); } });
        else saveRule(body);
      });
      on("[data-unblock-doc]", "click", function (btn) {
        ui().request("/api/contribution-blocks/" + encodeURIComponent(btn.getAttribute("data-unblock-doc")), { method: "DELETE" }).then(function (res) {
          if (!res.ok) { fail(res); return; }
          loadAll();
        });
      });

      on("[data-fic-contrib]", "click", function (btn) {
        dlgs().openContribPreview({
          documentId: btn.getAttribute("data-fic-contrib"),
          documentTitle: btn.getAttribute("data-fic-title"),
          theme: btn.getAttribute("data-fic-theme"),
          themeLabel: btn.getAttribute("data-fic-tlabel"),
          homeHref: homeHref,
          onSubmitted: function () { loadAll(); }
        });
      });

      on("[data-leave-kb]", "click", function (btn) {
        ui().request(kbUrl(btn.getAttribute("data-leave-kb"), "membership"), { method: "DELETE" }).then(function (res) {
          if (!res.ok) { fail(res); return; }
          loadAll();
        });
      });

      on("#kb-mutual-form", "submit", function (_form, ev) {
        ev.preventDefault();
        var val = host.querySelector("#kb-mutual-acc").value.trim();
        if (!val) return;
        // POST with the public number of the account. The request needs a recent sign-in: the request helper opens the re-authentication dialog.
        ui().request("/api/mutual-collaborations", { method: "POST", body: { account: val } }).then(function (res) {
          if (!res.ok) { fail(res); return; }
          loadAll();
        });
      });
      on("[data-mut-accept]", "click", function (btn) {
        ui().request("/api/mutual-collaborations/" + encodeURIComponent(btn.getAttribute("data-mut-accept")), { method: "PUT" }).then(function (r) {
          if (!r.ok) { fail(r); return; }
          loadAll();
        });
      });
      on("[data-mut-end]", "click", function (btn) {
        ui().request("/api/mutual-collaborations/" + encodeURIComponent(btn.getAttribute("data-mut-end")), { method: "DELETE" }).then(function (r) {
          if (!r.ok) { fail(r); return; }
          loadAll();
        });
      });
    }

    loadAll();
  }

  function renderSettingsKbManage(host, bs) {
    var currentNum = String((bs && bs.kb_number) || "");
    var isAdmin = !!(bs && bs.is_admin);
    var activeTab = "queue";
    var selectedPending = null;
    var selectedSource = "";
    var bound = false;
    var view = null;
    var managedList = null;

    function canReview(k) { return isAdmin || k.role === "moderator" || k.role === "collaborator"; }

    function showNone(res) {
      host.innerHTML = '<div class="claire-card"><p class="claire-hint">' + esc(!res || res.ok ? t("console.js.kb.no_managed_kbs") : ui().errorText(res)) + "</p>" +
        '<div class="claire-actions"><a class="claire-btn" href="/kb">' + esc(t("kb.pub.nav_kbs")) + '</a><a class="claire-btn sec" href="/settings/kb">' + esc(t("kb.pub.nav_settings")) + "</a></div></div>";
    }

    // The list of the selector, read one time: the admin is a reviewer of each shared knowledge base; each other account reviews where it is a collaborator or a moderator.
    function loadManaged() {
      if (managedList) return Promise.resolve({ ok: true });
      return ui().request(isAdmin ? "/kb" : "/kb?joined=true").then(function (res) {
        if (res.ok) managedList = ((res.data && res.data.knowledge_bases) || []).filter(canReview);
        return res;
      });
    }

    function load() {
      loadManaged().then(function (listRes) {
        var managed = managedList || [];
        if (!currentNum && managed.length) currentNum = String(managed[0].number);
        if (!currentNum) { showNone(listRes); return; }
        // GET /kb/{number}/ with "Accept: application/json" gives {"knowledge_base": {...}}: the item of this knowledge base with "role" and "membership_state" of the requester.
        Promise.all([
          ui().request(kbUrl(currentNum)),
          ui().request(kbUrl(currentNum, "contributions")),
          ui().request(kbUrl(currentNum, "members")),
          ui().request("/themes")
        ]).then(function (sub) {
          var kb = sub[0].ok && sub[0].data ? sub[0].data.knowledge_base : null;
          if (!kb || kb.is_personal || !canReview(kb)) { showNone(sub[0]); return; }
          // The selector shows the current data of the knowledge base of the page, also when the list does not have it.
          if (managed.some(function (k) { return String(k.number) === String(kb.number); })) managed = managed.map(function (k) { return String(k.number) === String(kb.number) ? kb : k; });
          else managed = managed.concat([kb]);
          managedList = managed;
          view = {
            managed: managed,
            kb: kb,
            contributions: (sub[1].ok && sub[1].data && sub[1].data.contributions) || [],
            members: (sub[2].ok && sub[2].data && sub[2].data.members) || [],
            themes: ((sub[3].ok && sub[3].data && sub[3].data.themes) || []).filter(function (th) { return th.state === "active"; }),
            sources: null,
            log: null
          };
          draw();
          loadSources();
          if (isAdmin || kb.role === "moderator") { loadLog(); loadRemoteGrants(); }
        });
      });
    }

    // GET /kb/{number}/moderation-log (a moderator or the admin): the entries are in "log", the newest first.
    function loadLog() {
      var current = view;
      ui().request(kbUrl(currentNum, "moderation-log")).then(function (res) {
        if (view !== current) return;
        view.log = res.ok ? ((res.data && res.data.log) || []) : res;
        drawLog();
      });
    }

    function logActionLabel(action) {
      var key = "console.js.kb.log_action." + action;
      var text = t(key);
      return text === key ? String(action || "") : text;
    }

    function drawLog() {
      var box = host.querySelector("#kb-log-host");
      if (!box || view.log == null) return;
      if (!Array.isArray(view.log)) {
        box.innerHTML = '<div class="claire-note warn"><span>' + esc(ui().errorText(view.log)) + "</span></div>";
        return;
      }
      box.innerHTML = '<p class="claire-hint">' + esc(t("console.js.kb.log_hint")) + "</p>" + ui().renderTable({
        caption: t("console.js.kb.tab_log"),
        columns: [
          { label: t("console.js.kb.log_col_time"), render: function (e) { return esc(dateText(e.created_at)); } },
          { label: t("console.js.kb.log_col_actor"), render: function (e) { return e.actor ? esc(e.actor.display_name || e.actor.account) + ' <span class="claire-hint">' + esc(e.actor.account) + "</span>" : esc(t("console.js.kb.log_actor_none")); } },
          { label: t("console.js.kb.log_col_action"), render: function (e) { return '<span class="claire-tag">' + esc(logActionLabel(e.action)) + "</span>"; } },
          { label: t("console.js.kb.log_col_target"), render: function (e) { return e.target ? '<code class="pref-mono">' + esc(e.target) + "</code>" : "—"; } },
          { label: t("kb.dlg.remove_reason"), render: function (e) { return e.reason ? esc(reasonText(e.reason)) : "—"; } }
        ],
        rows: view.log
      });
      ui().enhance(box);
    }

    // Curation takes documents of another knowledge base of one theme of K (design Section 9.7). GET /themes/{number}/ gives the knowledge bases of each theme.
    function loadSources() {
      var current = view;
      Promise.all(current.themes.map(function (th) { return ui().request("/themes/" + encodeURIComponent(th.number) + "/"); })).then(function (pages) {
        var sources = {};
        pages.forEach(function (page) {
          var members = (page.ok && page.data && page.data.knowledge_bases) || [];
          if (!members.some(function (k) { return String(k.number) === currentNum; })) return;
          members.forEach(function (k) { if (k.number && String(k.number) !== currentNum) sources[String(k.number)] = k; });
        });
        if (view !== current) return;
        view.sources = Object.keys(sources).map(function (number) { return sources[number]; });
        if (!selectedSource && view.sources.length) selectedSource = String(view.sources[0].number);
        drawCuration();
      });
      loadRemoteSources();
    }

    // Curation across servers (federation design Section 11). GET /kb/{number}/remote-sources gives the shared knowledge bases of other servers in the themes of K ("sources", each with the follow state of this server) and the newest requests to copy ("curations").
    function loadRemoteSources() {
      var current = view;
      ui().request(kbUrl(currentNum, "remote-sources")).then(function (res) {
        if (view !== current) return;
        view.remoteSources = (res.ok && res.data && res.data.sources) || [];
        view.remoteCurations = (res.ok && res.data && res.data.curations) || [];
        if (view.sources) drawCuration();
      });
    }

    // The key of a source in the list: the number of a knowledge base of this server, or "remote:<id>" for the record of a knowledge base of another server.
    function remoteSourceId(key) { return key && key.indexOf("remote:") === 0 ? Number(key.slice(7)) : null; }

    function drawRemoteDocuments(docsBox, key) {
      var remoteId = remoteSourceId(key);
      var requests = (view.remoteCurations || []);
      var requestsHtml = requests.length ? '<div style="margin-top:14px"><strong style="font-size:12.5px">' + esc(t("console.js.kb.curate_remote_requests")) + "</strong>" + ui().renderTable({
        caption: t("console.js.kb.curate_remote_requests"),
        columns: [
          { label: t("kb.dlg.col_document"), render: function (c) { return esc(c.title || c.document); } },
          { label: t("kb.dlg.col_state"), render: function (c) { return '<span class="claire-tag ' + (c.state === "done" ? "ok" : c.state === "failed" ? "warn" : "") + '">' + esc(t("console.js.kb.curate_remote_state." + c.state)) + "</span>" + (c.error ? " " + esc(ui().errorText({ error: c.error })) : ""); } }
        ],
        rows: requests
      }) + "</div>" : "";
      ui().request(kbUrl(currentNum, "remote-sources") + "?source=" + encodeURIComponent(remoteId)).then(function (r) {
        if (key !== selectedSource || !docsBox.isConnected) return;
        if (!r.ok) { docsBox.innerHTML = '<div class="claire-note warn">' + esc(ui().errorText(r)) + "</div>"; return; }
        var sel = (r.data && r.data.selected) || { state: "none", documents: [] };
        var docs = sel.documents || [];
        var reload = '<div class="claire-actions"><button type="button" class="claire-btn claire-btn-sm" id="kb-curate-reload">' + esc(t("console.js.kb.curate_remote_reload")) + "</button></div>";
        var note = sel.state === "failed" ? '<div class="claire-note warn"><span>' + esc(t("console.js.kb.curate_remote_failed", { code: sel.error || "" })) + "</span></div>" :
          sel.state !== "ready" ? '<p class="claire-hint">' + esc(t("console.js.kb.curate_remote_reading")) + "</p>" : "";
        if (!docs.length) {
          docsBox.innerHTML = note + (sel.state === "ready" ? '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>" : "") + reload + requestsHtml;
        } else {
          docsBox.innerHTML = note + '<form id="kb-curate-form" class="claire-panel-inner">' + docs.map(function (d) {
            // A document that this knowledge base has (or removed) is not selected again.
            return '<label class="claire-srow"><input type="checkbox" name="doc" value="' + esc(d.iri) + '"' + (d.curated ? " disabled" : "") + '/><span class="claire-srow-info"><strong>' + esc(d.title || d.iri) + "</strong>" +
              (d.curated ? ' <span class="claire-tag">' + esc(t("console.js.kb.curate_remote_held")) + "</span>" : "") + "</span></label>";
          }).join("") + '<div id="kb-curate-result"></div><div class="claire-actions"><button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.curate_submit")) + "</button>" +
            '<button type="button" class="claire-btn" id="kb-curate-reload">' + esc(t("console.js.kb.curate_remote_reload")) + "</button></div></form>" + requestsHtml;
          docsBox.querySelector("#kb-curate-form").addEventListener("submit", function (ev) {
            ev.preventDefault();
            var checked = Array.prototype.slice.call(docsBox.querySelectorAll('input[name="doc"]:checked')).map(function (x) { return x.value; });
            if (!checked.length) return;
            ui().request(kbUrl(currentNum, "curations"), { method: "POST", body: { remote_source: remoteId, documents: checked } }).then(function (cr) {
              if (!cr.ok) { ui().toast(ui().errorText(cr) + (cr.data && cr.data.limit ? " (" + cr.data.limit + ")" : ""), "danger"); return; }
              // Each document has its own result: a request that the server sends to the other server, or an error code.
              var results = (cr.data && cr.data.curations) || [];
              var errors = results.filter(function (x) { return x.error; });
              ui().toast(t("console.js.kb.curate_remote_queued", { count: results.length - errors.length }) + (errors.length ? " " + errors.map(function (x) { return ui().errorText({ error: x.error }); }).join(", ") : ""), errors.length ? "warn" : "ok");
              loadRemoteSources();
            });
          });
        }
        ui().enhance(docsBox);
        var reloadBtn = docsBox.querySelector("#kb-curate-reload");
        // The server reads the list from the other server; a new request shows the result, and the contributions that the requests made.
        if (reloadBtn) reloadBtn.addEventListener("click", function () { load(); });
      });
    }

    function docTitle(c) { return c.title || String(c.document || "—"); }
    // The contributor: an account of this server, or the actor of an account of another server (the credit of a copy, federation design Section 10.1). An account that is gone is "deleted account".
    function contributorName(c) {
      if (c.contributor) return c.contributor.display_name || c.contributor.account;
      return (c.remote && c.remote.credit) || t("kb.pub.contributor_gone");
    }

    function draw() {
      var kb = view.kb;
      var isMod = isAdmin || kb.role === "moderator";
      var archived = kb.state === "archived";
      // GET /kb/{number}/contributions gives the queue order to a reviewer only with "?state=pending"; here the order is the same: the level good first, low last, then the oldest first (design Section 8.6).
      var rank = { good: 0, limited: 1, low: 2 };
      var pending = view.contributions.filter(function (c) { return c.state === "pending"; }).sort(function (a, b) {
        var ra = rank[(a.contributor_reputation || {}).level], rb = rank[(b.contributor_reputation || {}).level];
        ra = ra == null ? 1 : ra; rb = rb == null ? 1 : rb;
        return ra !== rb ? ra - rb : Number(a.contributed_at || 0) - Number(b.contributed_at || 0);
      });
      // A copy whose origin removed the document (federation design Section 10.3): it is hidden and waits here for the decision of a reviewer.
      function originWaits(c) { return !!(c.origin_removed && c.origin_removed.waiting); }
      var originQueue = view.contributions.filter(originWaits);
      pending = pending.concat(originQueue);
      var others = view.contributions.filter(function (c) { return c.state !== "pending"; });
      var sel = pending.find(function (c) { return String(c.number) === String(selectedPending); }) || pending[0] || null;
      selectedPending = sel ? String(sel.number) : null;

      var head = '<div class="claire-card"><div class="claire-card-head">' +
        '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
        '<label for="rkb" style="font-weight:600;font-size:13px">' + esc(t("console.js.kb.switch_kb")) + "</label>" +
        '<select id="rkb" class="claire-select" style="width:auto;min-width:220px">' +
        view.managed.map(function (k) { return '<option value="' + esc(k.number) + '"' + (String(k.number) === currentNum ? " selected" : "") + ">" + esc(k.label) + " (" + esc(k.number) + ")</option>"; }).join("") + "</select>" +
        '<span class="claire-tag ' + (isMod ? "ok" : "acc") + '">' + esc(kb.role ? roleLabel(kb.role) : t("kb.role.admin")) + "</span>" + (archived ? " " + stateTag("archived") : "") +
        '</div><a class="claire-btn claire-btn-sm" href="' + esc(kbUrl(currentNum)) + '">' + esc(t("console.js.kb.open_kb")) + "</a></div>" +
        (archived ? '<div class="claire-note warn"><span>' + esc(t("kb.err.kb_archived")) + "</span></div>" : "") + "</div>";

      var queue = '<div class="claire-split"><div class="claire-list-col">' +
        (pending.length ? pending.map(function (c) {
          var level = (c.contributor_reputation || {}).level;
          return '<button type="button" class="claire-list-item' + (sel && String(c.number) === String(sel.number) ? " active" : "") + '" data-pick-pending="' + esc(c.number) + '">' +
            '<div class="claire-list-item-row"><strong>' + esc(docTitle(c)) + "</strong>" + (level ? levelTag(level) : "") + (originWaits(c) ? '<span class="claire-tag warn">' + esc(t("console.js.kb.queue_origin_removed")) + "</span>" : "") + "</div>" +
            '<div class="claire-list-item-sub"><span>' + esc(contributorName(c)) + "</span>" + (c.offer ? "<span>" + esc(t("console.js.kb.curate_remote_tag", { server: c.offer.server })) + "</span>" : "") + "<span>" + esc(t("kb.origin." + c.origin)) + "</span></div></button>";
        }).join("") : '<div class="claire-card"><p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p></div>") +
        '</div><div class="claire-detail-col">';
      if (sel && originWaits(sel)) {
        // The two decisions: show the copy again, or remove it (a moderator or the admin). A removal here never changes the origin.
        queue += '<div class="claire-card"><div class="claire-card-head"><h3>' + esc(docTitle(sel)) + "</h3>" +
          '<div class="claire-actions"><a class="claire-btn claire-btn-sm" href="' + esc(kbUrl(currentNum) + "?doc=" + encodeURIComponent(sel.document)) + '">' + esc(t("console.js.kb.queue_open_doc")) + "</a>" +
          // An archived knowledge base is read-only: the server refuses the two decisions with 403 kb_archived, so the page does not show their controls.
          (archived ? "" : '<button type="button" class="claire-btn pri claire-btn-sm" data-q-origin-keep="' + esc(sel.number) + '">' + esc(t("console.js.kb.queue_origin_keep")) + "</button>") +
          (isMod && !archived ? '<button type="button" class="claire-btn danger claire-btn-sm" data-q-origin-remove="' + esc(sel.number) + '">' + esc(t("console.js.kb.content_remove")) + "</button>" : "") + "</div></div>" +
          '<div class="claire-note warn"><span>' + esc(t("console.js.kb.queue_origin_hint")) + "</span></div>" +
          '<dl class="claire-kv" style="display:grid;grid-template-columns:200px 1fr;gap:6px 12px;font-size:13px;margin:0">' +
          "<dt>" + esc(t("console.js.kb.queue_origin_reason")) + "</dt><dd>" + esc(reasonText(sel.origin_removed.reason)) + "</dd>" +
          (sel.remote ? "<dt>" + esc(t("console.js.kb.queue_origin_source")) + '</dt><dd><code class="pref-mono">' + esc(sel.remote.document) + "</code></dd>" : "") +
          "</dl></div>";
      } else if (sel) {
        var help = sel.review_help || null;
        var rep = sel.contributor_reputation || null;
        queue += '<div class="claire-card"><div class="claire-card-head"><h3>' + esc(docTitle(sel)) + "</h3>" +
          '<div class="claire-actions"><a class="claire-btn claire-btn-sm" href="' + esc(kbUrl(currentNum) + "?doc=" + encodeURIComponent(sel.document)) + '">' + esc(t("console.js.kb.queue_open_doc")) + "</a>" +
          // An archived knowledge base is read-only (design Section 10.1): the server refuses review, curation, and removal with 403 kb_archived, so the page does not show their controls.
          (archived ? "" : '<button type="button" class="claire-btn pri claire-btn-sm" data-q-approve="' + esc(sel.number) + '">' + esc(t("console.js.kb.queue_approve")) + "</button>" +
          '<button type="button" class="claire-btn danger claire-btn-sm" data-q-reject="' + esc(sel.number) + '">' + esc(t("console.js.kb.queue_reject")) + "</button>") + "</div></div>";
        queue += '<dl class="claire-kv" style="display:grid;grid-template-columns:200px 1fr;gap:6px 12px;font-size:13px;margin:0">' +
          "<dt>" + esc(t("kb.dlg.col_contributor")) + "</dt><dd>" + esc(contributorName(sel)) + "</dd>" +
          // An offer of an account of another server (federation design Sections 12.1 and 12.2): its server, and if its grant of the role collaborator was in effect when the offer came.
          (sel.offer ? "<dt>" + esc(t("console.js.kb.queue_remote_server")) + "</dt><dd>" + esc(sel.offer.server) + "</dd>" +
            "<dt>" + esc(t("console.js.kb.queue_grant")) + "</dt><dd>" + '<span class="claire-tag ' + (sel.offer.grant_valid ? "ok" : (sel.offer.with_grant ? "warn" : "")) + '">' +
            esc(t(sel.offer.grant_valid ? "console.js.kb.queue_grant_valid" : (sel.offer.with_grant ? "console.js.kb.queue_grant_invalid" : "console.js.kb.queue_grant_none"))) + "</span>" +
            // A moderator can give the role collaborator to this account from here (capability "people"); the grant goes to the next offers of the account.
            (isMod && sel.offer.actor ? ' <button type="button" class="claire-btn claire-btn-sm" data-q-remote-grant="' + esc(sel.offer.actor) + '">' + esc(t("console.js.kb.remote_collab_give")) + "</button>" : "") + "</dd>" : "") +
          (rep ? "<dt>" + esc(t("console.js.kb.queue_reputation")) + "</dt><dd>" + levelTag(rep.level) + " " + esc(t("console.js.kb.queue_approval_rate", { rate: percent(rep.approval_rate) })) + "</dd>" : "") +
          (help ? "<dt>" + esc(t("console.js.kb.queue_route")) + "</dt><dd>" + esc(help.routed_by ? t("kb.routed." + help.routed_by) : "—") + "</dd>" +
            "<dt>" + esc(t("console.js.kb.queue_theme_score")) + "</dt><dd>" + esc(help.theme_score != null ? Number(help.theme_score).toFixed(2) : "—") + "</dd>" +
            "<dt>" + esc(t("console.js.kb.queue_kb_score")) + "</dt><dd>" + esc(help.kb_score != null ? Number(help.kb_score).toFixed(2) : "—") + "</dd>" : "") +
          "</dl>";
        if (help && (help.other_decisions || []).length) {
          queue += '<div><strong style="font-size:12.5px">' + esc(t("console.js.kb.queue_other_decisions")) + "</strong>" +
            ui().renderTable({
              caption: t("console.js.kb.queue_other_decisions"),
              columns: [
                { label: t("kb.dlg.col_kb"), render: function (d) { return esc(d.label || d.knowledge_base); } },
                { label: t("console.js.kb.queue_decision"), render: function (d) { return '<span class="claire-tag">' + esc(t("kb.decision." + d.decision)) + "</span>"; } },
                { label: t("kb.dlg.remove_reason"), render: function (d) { return d.reason ? esc(ui().errorText({ error: d.reason })) : "—"; } }
              ],
              rows: help.other_decisions
            }) + "</div>";
        }
        queue += "</div>";
      } else {
        queue += '<div class="claire-card"><p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p></div>";
      }
      queue += "</div></div>";

      var content = '<div class="claire-card">' + ui().renderTable({
        caption: t("console.js.kb.tab_content"),
        columns: [
          { label: t("kb.dlg.col_document"), render: function (c) { return "<strong>" + esc(docTitle(c)) + "</strong> <span class=\"claire-hint\">" + esc(t("kb.origin." + c.origin)) + "</span>"; } },
          { label: t("kb.dlg.col_contributor"), render: function (c) { return esc(contributorName(c)); } },
          { label: t("kb.dlg.col_state"), render: function (c) { return stateTag(c.state) + (c.reason ? " " + esc(ui().errorText({ error: c.reason })) : "") + (c.updates_stopped ? ' <span class="claire-tag warn">' + esc(t("console.js.kb.content_frozen")) + "</span>" : ""); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (c) {
            var items = [];
            if (archived) return "—";
            if (c.state === "visible") items.push({ action: "hide", value: c.number, label: t("console.js.kb.content_hide"), icon: "eye" });
            if (c.state === "hidden") items.push({ action: "unhide", value: c.number, label: t("console.js.kb.content_unhide"), icon: "eye" });
            if (c.state === "visible" || c.state === "hidden") items.push({ action: c.updates_stopped ? "restart_updates" : "stop_updates", value: c.number, label: t(c.updates_stopped ? "console.js.kb.content_unfreeze" : "console.js.kb.content_freeze"), icon: "lock" });
            if (c.state === "failed") items.push({ action: "retry", value: c.number, label: t("console.js.kb.content_retry"), icon: "restore" });
            // Remove needs the capability "remove": a moderator or the admin (design Section 6.2).
            if (isMod && (c.state === "visible" || c.state === "hidden" || c.state === "failed")) {
              if (items.length) items.push({ sep: true });
              items.push({ action: "remove", value: c.number, label: t("console.js.kb.content_remove"), icon: "trash", danger: true });
            }
            return items.length ? ui().renderRowMenu(items, docTitle(c)) : "—";
          } }
        ],
        rows: others
      }) + "</div>";

      var tabs = [
        { id: "queue", label: t("console.js.kb.tab_queue"), badge: pending.length, icon: "inbox", html: queue },
        { id: "content", label: t("console.js.kb.tab_content"), icon: "doc", html: content }
      ];
      if (!archived) tabs.push({ id: "curate", label: t("console.js.kb.tab_curate"), icon: "puzzle", html: '<div id="kb-curate-host"><p class="claire-hint">' + esc(t("kb.ui.loading")) + "</p></div>" });

      if (isMod) {
        var people = '<div class="claire-card">' + ui().renderTable({
          caption: t("console.js.kb.tab_people"),
          columns: [
            { label: t("kb.dlg.col_account"), render: function (m) { return "<strong>" + esc(m.display_name || m.account) + "</strong>" + (m.username ? ' <span class="claire-hint">@' + esc(m.username) + "</span>" : "") + ' <span class="claire-hint">' + esc(m.account) + "</span>"; } },
            { label: t("kb.pub.col_role"), render: function (m) { return '<span class="claire-tag acc">' + esc(roleLabel(m.role)) + "</span>"; } },
            { label: t("kb.dlg.col_state"), render: function (m) { return esc(t("kb.member_state." + m.state)); } },
            { label: t("kb.pub.col_actions"), actions: true, render: function (m) {
              var items = [];
              if (m.state === "pending") items.push({ action: "mem_approve", value: m.account, label: t("console.js.kb.people_approve"), icon: "check" });
              if (m.state === "active" && m.role === "member") items.push({ action: "mem_collab", value: m.account, label: t("console.js.kb.people_grant_collab"), icon: "user" });
              if (m.state === "active" && m.role === "collaborator") items.push({ action: "mem_member", value: m.account, label: t("console.js.kb.people_revoke_collab"), icon: "user" });
              // Only the admin gives and removes the role "moderator" (design Section 10.2).
              if (isAdmin && m.state === "active" && m.role !== "moderator") items.push({ action: "mem_mod_give", value: m.account, label: t("console.js.kb.people_give_mod"), icon: "user" });
              if (isAdmin && m.role === "moderator") items.push({ action: "mem_mod_remove", value: m.account, label: t("console.js.kb.people_remove_mod"), icon: "user" });
              if (m.role !== "moderator" || isAdmin) {
                if (items.length) items.push({ sep: true });
                items.push({ action: "mem_remove", value: m.account, label: t(m.state === "banned" ? "console.js.kb.people_unban" : "console.js.kb.people_remove"), icon: "trash" });
                if (m.state !== "banned") items.push({ action: "mem_ban", value: m.account, label: t("console.js.kb.people_ban"), icon: "block", danger: true });
              }
              return items.length ? ui().renderRowMenu(items, m.display_name || m.account) : "—";
            } }
          ],
          rows: view.members
        }) + "</div>";

        var seeds = Array.isArray(kb.seed_entities) ? kb.seed_entities.join(", ") : "";
        var settings = '<form class="claire-card" id="kb-manage-settings-form">' +
          '<div class="claire-field"><label class="claire-field-label" for="mset-label">' + esc(t("kb.dlg.field_name")) + '</label><input id="mset-label" class="claire-input" maxlength="80" value="' + esc(kb.label || "") + '" required/></div>' +
          '<div class="claire-field"><label class="claire-field-label" for="mset-desc">' + esc(t("kb.dlg.field_desc")) + '</label><textarea id="mset-desc" class="claire-textarea">' + esc(kb.description || "") + "</textarea></div>" +
          '<div class="claire-field"><label class="claire-field-label" for="mset-type">' + esc(t("kb.dlg.field_type")) + '</label><select id="mset-type" class="claire-select">' +
          '<option value="real"' + (kb.knowledge_type === "real" ? " selected" : "") + ">" + esc(t("kb.dlg.type_real")) + "</option>" +
          '<option value="fictional"' + (kb.knowledge_type === "fictional" ? " selected" : "") + ">" + esc(t("kb.dlg.type_fictional")) + "</option></select>" +
          '<span class="claire-hint">' + esc(t("console.js.kb.settings_type_hint")) + "</span></div>" +
          '<div class="claire-field"><label class="claire-field-label" for="mset-seeds">' + esc(t("kb.dlg.field_seeds")) + '</label><input id="mset-seeds" class="claire-input" value="' + esc(seeds) + '"/></div>' +
          '<div class="claire-field"><label class="claire-field-label" for="mset-policy">' + esc(t("kb.pub.col_policy")) + '</label><select id="mset-policy" class="claire-select">' +
          '<option value="open"' + (kb.join_policy === "open" ? " selected" : "") + ">" + esc(t("console.js.kb.settings_join_open")) + "</option>" +
          '<option value="approval"' + (kb.join_policy === "approval" ? " selected" : "") + ">" + esc(t("console.js.kb.settings_join_approval")) + "</option></select></div>" +
          '<div class="claire-actions"><button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.settings_save")) + "</button>" +
          '<button type="button" class="claire-btn sec" id="kb-archive-btn">' + esc(t(archived ? "console.js.kb.settings_unarchive" : "console.js.kb.settings_archive")) + "</button>" +
          (isAdmin ? '<button type="button" class="claire-btn danger" id="kb-delete-btn">' + esc(t("console.js.kb.settings_delete")) + "</button>" : "") +
          "</div></form>";

        // Collaborators of other servers (federation design Section 12.2): GET /kb/{number}/remote-collaborators; a grant has a term of 6 months, and Revoke ends it at once. The changes need a recent sign-in.
        people += '<div class="claire-card" id="kb-remote-collab-host"><div class="claire-card-head"><h3>' + ui().icon("globe") + " " + esc(t("console.js.kb.remote_collab_card")) + "</h3></div>" +
          '<p class="claire-hint">' + esc(t("console.js.kb.remote_collab_hint")) + "</p>" +
          '<form id="kb-remote-collab-form" style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-end">' +
            '<div class="claire-field" style="flex:1;min-width:260px"><label class="claire-field-label" for="kb-remote-collab-actor">' + esc(t("console.js.kb.remote_collab_actor")) + '</label><input id="kb-remote-collab-actor" class="claire-input" type="url" maxlength="2048" placeholder="https://example.org/ap/actors/…" required/></div>' +
            '<button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.remote_collab_give")) + "</button></form>" +
          '<div id="kb-remote-collab-list"><p class="claire-hint">' + esc(t("kb.ui.loading")) + "</p></div></div>";
        tabs.push({ id: "people", label: t("console.js.kb.tab_people"), icon: "users", html: people });
        tabs.push({ id: "settings", label: t("console.js.kb.tab_settings"), icon: "settings", html: settings });
        tabs.push({ id: "log", label: t("console.js.kb.tab_log"), icon: "doc", html: '<div class="claire-card" id="kb-log-host"><p class="claire-hint">' + esc(t("kb.ui.loading")) + "</p></div>" });
      }

      host.innerHTML = head + ui().renderTabs({ id: "kb-manage-tabs", ariaLabel: kb.label || currentNum, active: activeTab, tabs: tabs });
      ui().enhance(host);
      if (view.sources) drawCuration();
      drawLog();
      drawRemoteGrants();
      bind();
    }

    // GET /kb/{number}/remote-collaborators (a moderator or the admin): the grants of this knowledge base, the newest first.
    function loadRemoteGrants() {
      var current = view;
      ui().request(kbUrl(currentNum, "remote-collaborators")).then(function (res) {
        if (view !== current) return;
        view.remoteGrants = res.ok ? ((res.data && res.data.grants) || []) : res;
        drawRemoteGrants();
      });
    }

    function drawRemoteGrants() {
      var box = host.querySelector("#kb-remote-collab-list");
      if (!box || view.remoteGrants == null) return;
      if (!Array.isArray(view.remoteGrants)) {
        box.innerHTML = '<div class="claire-note warn"><span>' + esc(ui().errorText(view.remoteGrants)) + "</span></div>";
        return;
      }
      box.innerHTML = ui().renderTable({
        caption: t("console.js.kb.remote_collab_card"),
        columns: [
          { label: t("kb.dlg.col_account"), render: function (g) { return '<code class="pref-mono">' + esc(g.actor) + "</code>"; } },
          { label: t("console.js.kb.queue_remote_server"), render: function (g) { return esc(g.server); } },
          { label: t("kb.dlg.col_state"), render: function (g) { return grantStateTag(g.state); } },
          { label: t("console.js.kb.grants_col_end"), render: function (g) { return esc(dateText(g.state === "revoked" ? g.revoked_at : g.end_time)); } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (g) {
            // A renewal is a new grant of 6 months; the server revokes the grant that is in effect.
            var btns = '<button type="button" class="claire-btn claire-btn-sm" data-remote-grant-renew="' + esc(g.actor) + '">' + esc(t(g.state === "active" ? "console.js.kb.remote_collab_renew" : "console.js.kb.remote_collab_give")) + "</button>";
            if (g.state === "active") btns += ' <button type="button" class="claire-btn danger claire-btn-sm" data-remote-grant-revoke="' + esc(g.id) + '">' + esc(t("console.js.kb.remote_collab_revoke")) + "</button>";
            return btns;
          } }
        ],
        rows: view.remoteGrants
      });
      ui().enhance(box);
      box.querySelectorAll("[data-remote-grant-renew]").forEach(function (btn) {
        btn.addEventListener("click", function () { btn.disabled = true; giveRemoteGrant(btn.getAttribute("data-remote-grant-renew")); });
      });
      box.querySelectorAll("[data-remote-grant-revoke]").forEach(function (btn) {
        btn.addEventListener("click", function () {
          btn.disabled = true;
          // A change of a role needs a recent sign-in: the request helper opens the re-authentication dialog for 401 "reauth_required".
          ui().request(kbUrl(currentNum, "remote-collaborators/" + encodeURIComponent(btn.getAttribute("data-remote-grant-revoke"))), { method: "DELETE" }).then(function (r) {
            if (!r.ok) { btn.disabled = false; fail(r); return; }
            ui().toast(t("console.js.kb.remote_collab_revoked"), "ok");
            loadRemoteGrants();
          });
        });
      });
    }

    function giveRemoteGrant(actor) {
      return ui().request(kbUrl(currentNum, "remote-collaborators"), { method: "POST", body: { actor: actor } }).then(function (r) {
        if (!r.ok) { fail(r); drawRemoteGrants(); return false; }
        ui().toast(t("console.js.kb.remote_collab_given"), "ok");
        loadRemoteGrants();
        return true;
      });
    }

    function drawCuration() {
      var box = host.querySelector("#kb-curate-host");
      if (!box) return;
      var sources = view.sources || [];
      var remoteSources = view.remoteSources || [];
      if (!sources.length && !remoteSources.length) {
        box.innerHTML = '<div class="claire-card"><p class="claire-hint">' + esc(t("console.js.kb.curate_no_source")) + "</p></div>";
        return;
      }
      if (!selectedSource) selectedSource = sources.length ? String(sources[0].number) : "remote:" + remoteSources[0].id;
      box.innerHTML = '<div class="claire-split"><div class="claire-list-col"><div class="claire-card"><strong style="font-size:13px">' + esc(t("console.js.kb.curate_source")) + "</strong>" +
        sources.map(function (source) {
          return '<button type="button" class="claire-list-item' + (String(source.number) === selectedSource ? " active" : "") + '" data-curate-src="' + esc(source.number) + '">' +
            "<strong>" + esc(source.label) + '</strong><span class="claire-list-item-sub">' + esc(t(source.is_personal ? "themes.kb_personal" : "themes.kb_shared")) + "</span></button>";
        }).join("") +
        // The shared knowledge bases of other servers in the themes of K, with the follow state of this server for each one.
        remoteSources.map(function (source) {
          var key = "remote:" + source.id;
          return '<button type="button" class="claire-list-item' + (key === selectedSource ? " active" : "") + '" data-curate-src="' + esc(key) + '">' +
            "<strong>" + esc(source.label) + '</strong><span class="claire-list-item-sub"><span>' + esc(t("console.js.kb.curate_remote_tag", { server: source.server })) + "</span>" +
            (source.follow ? "<span>" + esc(t("console.js.kb.curate_follow." + source.follow)) + "</span>" : "") + "</span></button>";
        }).join("") + '</div></div><div class="claire-detail-col"><div class="claire-card" id="kb-curate-docs"><p class="claire-hint">' + esc(t("kb.ui.loading")) + "</p></div></div></div>";
      box.querySelectorAll("[data-curate-src]").forEach(function (btn) {
        btn.addEventListener("click", function () { selectedSource = btn.getAttribute("data-curate-src"); drawCuration(); });
      });
      var docsBox = box.querySelector("#kb-curate-docs");
      var source = selectedSource;
      if (remoteSourceId(source) !== null) { drawRemoteDocuments(docsBox, source); return; }
      // The visible documents of the source. For a personal knowledge base the server sends 308 from "/kb/<number>/documents" to the path of its account.
      ui().request(kbUrl(source, "documents")).then(function (r) {
        if (source !== selectedSource || !docsBox.isConnected) return;
        var docs = ((r.ok && r.data && r.data.documents) || []).filter(function (d) { return d.hidden !== 1 && d.hidden !== true; });
        if (!r.ok) { docsBox.innerHTML = '<div class="claire-note warn">' + esc(ui().errorText(r)) + "</div>"; return; }
        if (!docs.length) { docsBox.innerHTML = '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>"; return; }
        docsBox.innerHTML = '<form id="kb-curate-form" class="claire-panel-inner">' + docs.map(function (d) {
          return '<label class="claire-srow"><input type="checkbox" name="doc" value="' + esc(d.id) + '"/><span class="claire-srow-info"><strong>' + esc(d.title || d.id) + "</strong></span></label>";
        }).join("") + '<div id="kb-curate-result"></div><div class="claire-actions"><button type="submit" class="claire-btn pri">' + esc(t("console.js.kb.curate_submit")) + "</button></div></form>";
        docsBox.querySelector("#kb-curate-form").addEventListener("submit", function (ev) {
          ev.preventDefault();
          var checked = Array.prototype.slice.call(docsBox.querySelectorAll('input[name="doc"]:checked')).map(function (x) { return x.value; });
          if (!checked.length) return;
          ui().request(kbUrl(currentNum, "curations"), { method: "POST", body: { source: source, documents: checked } }).then(function (cr) {
            if (!cr.ok) { ui().toast(ui().errorText(cr) + (cr.data && cr.data.limit ? " (" + cr.data.limit + ")" : ""), "danger"); return; }
            // Each document has its own result: a contribution, or an error code.
            var results = (cr.data && cr.data.curations) || [];
            var errors = results.filter(function (x) { return x.error; });
            var resultBox = docsBox.querySelector("#kb-curate-result");
            if (resultBox) resultBox.innerHTML = errors.length ? '<div class="claire-note warn"><span>' + errors.map(function (x) { return esc(x.document) + ": " + esc(ui().errorText({ error: x.error })); }).join("<br>") + "</span></div>" : "";
            ui().toast(t("console.js.kb.curate_done", { count: results.length - errors.length }), errors.length ? "warn" : "ok");
            if (!errors.length) load();
          });
        });
      });
    }

    function review(number, body) {
      return ui().request(kbUrl(currentNum, "contributions/" + encodeURIComponent(number)), { method: "PATCH", body: body }).then(function (r) {
        if (!r.ok) { fail(r); return; }
        load();
      });
    }

    function memberChange(accountNumber, body) {
      // A role change needs a recent sign-in: the request helper opens the re-authentication dialog for 401 "reauth_required".
      return ui().request(kbUrl(currentNum, "members/" + encodeURIComponent(accountNumber)), { method: "PATCH", body: body }).then(function (r) {
        if (!r.ok) { fail(r); return; }
        load();
      });
    }

    function bind() {
      var tabRoot = host.querySelector("#kb-manage-tabs");
      if (tabRoot) tabRoot.addEventListener("claire:tabchange", function (ev) { activeTab = ev.detail.tab; });
      var switcher = host.querySelector("#rkb");
      if (switcher) switcher.addEventListener("change", function () {
        currentNum = switcher.value;
        selectedPending = null;
        selectedSource = "";
        if (window.history && window.history.replaceState) window.history.replaceState(null, "", "/settings/kb/" + encodeURIComponent(currentNum));
        load();
      });
      host.querySelectorAll("[data-pick-pending]").forEach(function (btn) {
        btn.addEventListener("click", function () { selectedPending = btn.getAttribute("data-pick-pending"); draw(); });
      });
      host.querySelectorAll("[data-q-approve]").forEach(function (btn) {
        btn.addEventListener("click", function () { btn.disabled = true; review(btn.getAttribute("data-q-approve"), { action: "approve" }); });
      });
      host.querySelectorAll("[data-q-origin-keep]").forEach(function (btn) {
        btn.addEventListener("click", function () { btn.disabled = true; review(btn.getAttribute("data-q-origin-keep"), { action: "unhide" }); });
      });
      host.querySelectorAll("[data-q-origin-remove]").forEach(function (btn) {
        btn.addEventListener("click", function () { dlgs().openRemoveContributionDialog({ kbNumber: currentNum, contribNumber: btn.getAttribute("data-q-origin-remove"), onRemoved: function () { load(); } }); });
      });
      host.querySelectorAll("[data-q-remote-grant]").forEach(function (btn) {
        btn.addEventListener("click", function () { btn.disabled = true; giveRemoteGrant(btn.getAttribute("data-q-remote-grant")).then(function () { btn.disabled = false; }); });
      });
      host.querySelectorAll("[data-q-reject]").forEach(function (btn) {
        btn.addEventListener("click", function () { dlgs().openRejectContributionDialog({ kbNumber: currentNum, contribNumber: btn.getAttribute("data-q-reject"), onRejected: function () { load(); } }); });
      });

      var grantForm = host.querySelector("#kb-remote-collab-form");
      if (grantForm) grantForm.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var input = host.querySelector("#kb-remote-collab-actor");
        if (!grantForm.reportValidity()) return;
        giveRemoteGrant(input.value.trim()).then(function (ok) { if (ok) input.value = ""; });
      });

      var form = host.querySelector("#kb-manage-settings-form");
      if (form) form.addEventListener("submit", function (ev) {
        ev.preventDefault();
        var seedsEl = host.querySelector("#mset-seeds");
        var seeds = dlgs().seedList(seedsEl.value);
        seedsEl.setCustomValidity(seeds.length > 20 ? t("kb.dlg.err_seeds_max") : "");
        if (!form.reportValidity()) return;
        var body = { label: host.querySelector("#mset-label").value.trim(), description: host.querySelector("#mset-desc").value.trim(), seed_entities: seeds, join_policy: host.querySelector("#mset-policy").value };
        if (host.querySelector("#mset-type").value !== view.kb.knowledge_type) body.knowledge_type = host.querySelector("#mset-type").value;
        ui().request(kbUrl(currentNum), { method: "PATCH", body: body }).then(function (r) {
          if (!r.ok) { fail(r); return; }
          ui().toast(t("console.js.kb.saved"), "ok");
          load();
        });
      });
      var archiveBtn = host.querySelector("#kb-archive-btn");
      if (archiveBtn) archiveBtn.addEventListener("click", function () {
        ui().request(kbUrl(currentNum), { method: "PATCH", body: { state: view.kb.state === "archived" ? "active" : "archived" } }).then(function (r) {
          if (!r.ok) { fail(r); return; }
          load();
        });
      });
      var deleteBtn = host.querySelector("#kb-delete-btn");
      if (deleteBtn) deleteBtn.addEventListener("click", function () {
        dlgs().openDeleteKbDialog({ kbNumber: currentNum, kbLabel: view.kb.label, onDeleted: function () { window.location.assign("/admin/kb"); } });
      });

      if (bound) return;
      bound = true;
      host.addEventListener("click", function (ev) {
        var item = ev.target && ev.target.closest ? ev.target.closest("[data-menu-action]") : null;
        if (!item) return;
        var action = item.getAttribute("data-menu-action");
        var value = item.getAttribute("data-menu-value");
        if (action === "remove") dlgs().openRemoveContributionDialog({ kbNumber: currentNum, contribNumber: value, onRemoved: function () { load(); } });
        else if (action === "hide" || action === "unhide" || action === "stop_updates" || action === "restart_updates") review(value, { action: action });
        else if (action === "retry") {
          ui().request(kbUrl(currentNum, "contributions/" + encodeURIComponent(value) + "/retry"), { method: "POST", body: {} }).then(function (r) {
            if (!r.ok) { fail(r); return; }
            load();
          });
        }
        else if (action === "mem_approve") memberChange(value, { action: "approve" });
        else if (action === "mem_ban") memberChange(value, { action: "ban" });
        else if (action === "mem_remove") memberChange(value, { action: "remove" });
        else if (action === "mem_collab") memberChange(value, { role: "collaborator" });
        else if (action === "mem_member") memberChange(value, { role: "member" });
        else if (action === "mem_mod_give" || action === "mem_mod_remove") {
          ui().request("/api/admin/kb/" + encodeURIComponent(currentNum) + "/moderators/" + encodeURIComponent(value), { method: action === "mem_mod_give" ? "PUT" : "DELETE" }).then(function (r) {
            if (!r.ok) { fail(r); return; }
            load();
          });
        }
      });
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
    if (kbHost && bs.section === "kb" && bs.area === "settings") renderSettingsKb(kbHost, bs);
    var manageHost = document.getElementById("settings-kb-manage-host");
    if (manageHost && bs.section === "kb_manage") renderSettingsKbManage(manageHost, bs);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window);
