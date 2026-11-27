/* Public views of the shared knowledge bases and the themes: /kb, /kb/{number}/, /themes, /themes/{number}/ (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 7 and 8.4). The data comes from the JSON form of the routes of docs/implementation/API_HTTP_CONTRACT.md. */
(function (root) {
  "use strict";

  function ui() { return root.ClaireUi; }
  function dlgs() { return root.ClaireKbDialogs; }
  function t(key, params) { return ui().t(key, params); }
  function esc(v) { return ui().esc(v); }

  function parseBootstrap() {
    var el = document.getElementById("claire-public-bootstrap");
    if (!el) return null;
    try { return JSON.parse(el.textContent || "{}"); } catch (_) { return null; }
  }

  function themeHref(number) { return "/themes/" + encodeURIComponent(number) + "/"; }
  function kbHref(number) { return "/kb/" + encodeURIComponent(number) + "/"; }
  function typeLabel(knowledgeType) { return t(knowledgeType === "fictional" ? "kb.knowledge_type.fictional" : "kb.knowledge_type.real"); }
  function knowledgeTypeTag(knowledgeType) { return '<span class="claire-tag ' + (knowledgeType === "fictional" ? "fic" : "acc") + '">' + esc(typeLabel(knowledgeType)) + "</span>"; }
  function loginHref() { return "/login?next=" + encodeURIComponent(window.location.pathname + window.location.search); }

  function publicNavHtml(current) {
    if (document.querySelector("header.appbar .pub-appbar-nav")) return "";
    var links = [
      { id: "home", href: "/", label: t("kb.pub.nav_home") },
      { id: "themes", href: "/themes", label: t("kb.pub.nav_themes") },
      { id: "kb", href: "/kb", label: t("kb.pub.nav_kbs") },
      { id: "accounts", href: "/directory", label: t("kb.pub.nav_accounts") },
      { id: "about", href: "/about", label: t("nav.about") }
    ];
    return '<nav class="claire-pubnav" aria-label="' + esc(t("kb.pub.nav_label")) + '">' + links.map(function (it) {
      return '<a href="' + esc(it.href) + '"' + (it.id === current ? ' aria-current="page"' : "") + ">" + esc(it.label) + "</a>";
    }).join("") + "</nav>";
  }

  function viewSwitchHtml(active) {
    return '<nav class="obs-view-switch" aria-label="' + esc(t("kb.pub.view_switch_label")) + '">' +
      '<a href="/themes"' + (active === "themes" ? ' aria-current="page"' : "") + ">" + esc(t("kb.pub.view_themes")) + "</a>" +
      '<a href="/kb"' + (active === "kb" ? ' aria-current="page"' : "") + ">" + esc(t("kb.pub.view_kbs")) + "</a>" +
      "</nav>";
  }

  function knowledgeTypeChips(name, value) {
    return ui().renderChips({
      name: name,
      label: t("kb.knowledge_type.label"),
      value: value,
      options: [
        { value: "all", label: t("kb.pub.filter_all") },
        { value: "real", label: t("kb.knowledge_type.real") },
        { value: "fictional", label: t("kb.knowledge_type.fictional") }
      ]
    });
  }

  function knowledgeTypeFromQuery() {
    var value = new URLSearchParams(window.location.search).get("knowledge_type");
    return value === "real" || value === "fictional" ? value : "all";
  }

  function setKnowledgeTypeQuery(value) {
    if (!window.history || !window.history.replaceState) return;
    window.history.replaceState(null, "", window.location.pathname + (value === "all" ? "" : "?knowledge_type=" + encodeURIComponent(value)));
  }

  // The root page of network mode has the themes from the server (design Section 8.4). This part adds the links to the collections.
  function renderHomeShowcase(host) {
    if (document.getElementById("home-observatory") || document.querySelector(".pub-appbar-nav")) return;
    host.innerHTML = publicNavHtml("home");
  }

  // A link to a page of another server: a new tab, and no data of this page goes to that server.
  function remoteLink(url, label) {
    return '<a href="' + esc(url) + '" target="_blank" rel="noopener noreferrer">' + esc(label) + "</a>";
  }

  // One item of the recent activity across servers. "remote_knowledge_base": a new shared knowledge base of another server. "accepted_contribution": another server approved a contribution of an account of this server ("account" is null when the account must not be named). "curation": a shared knowledge base of this server ("knowledge_base") copied documents of a knowledge base of another server. "label" and "url" are null when this server has no record of the knowledge base of the other server: then the item shows the server only.
  function recentItemHtml(it) {
    var remote = it.url && it.label ? remoteLink(it.url, it.label) : "";
    var server = '<span class="themes-count">(' + esc(it.server) + (it.kind === "remote_knowledge_base" ? " · " + esc(typeLabel(it.knowledge_type)) : "") + ")</span>";
    if (it.kind === "accepted_contribution") {
      var who = it.account ? " " + esc(t("kb.pub.recent_by")) + ' <a href="/@' + esc(encodeURIComponent(it.account.username)) + '/">' + esc(it.account.display_name || it.account.username) + "</a>" : "";
      return esc(t("kb.pub.recent_accepted")) + " " + remote + server + who;
    }
    if (it.kind === "curation") {
      var local = it.knowledge_base ? '<a href="' + esc(kbHref(it.knowledge_base.number)) + '">' + esc(it.knowledge_base.label) + "</a>" : "";
      return esc(t("kb.pub.recent_curation")) + " " + local + " ← " + remote + server;
    }
    return esc(t("kb.pub.recent_remote_kb")) + " " + remote + server;
  }

  // The root page of network mode, after the themes (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 3). The server puts the recent activity across servers in the JSON element "public-home-recent-data": {"recent": [items of recentItemHtml]}. With no recent activity, the part shows nothing.
  function renderHomeRecent(host) {
    var source = document.getElementById("public-home-recent-data");
    var recent = [];
    try { recent = (source && JSON.parse(source.textContent).recent) || []; } catch (e) { recent = []; }
    if (!recent.length) { host.innerHTML = ""; return; }
    host.innerHTML = '<section class="obs-recent-section" aria-labelledby="home-recent-h2">' +
      '<div class="obs-section-head"><h2 id="home-recent-h2">' + ui().icon("globe") + "<span>" + esc(t("kb.pub.recent_title")) + "</span></h2></div>" +
      '<ul class="obs-recent-list">' + recent.map(function (it) {
        return '<li data-federation-recent="' + esc(it.kind) + '">' + recentItemHtml(it) + "</li>";
      }).join("") + "</ul></section>";
    ui().enhance(host);
  }

  // GET /themes: the root of the collection has each theme in "themes" (number, url, label, description, knowledge_type, state, knowledge_bases = the count of knowledge bases of this server, remote_knowledge_bases = the count of shared knowledge bases of other servers).
  function renderThemesList(host, bs) {
    var knowledgeTypeFilter = knowledgeTypeFromQuery();
    var themes = [];
    var loadError = null;
    ui().request("/themes").then(function (r) {
      themes = ((r.ok && r.data && r.data.themes) || []).filter(function (th) { return th.state === "active"; });
      loadError = r.ok ? null : r;
      paint();
    });

    function paint() {
      var list = themes.filter(function (th) { return knowledgeTypeFilter === "all" || th.knowledge_type === knowledgeTypeFilter; });
      var totalLocalKbs = list.reduce(function (acc, th) { return acc + (Number(th.knowledge_bases) || 0); }, 0);
      var totalRemoteKbs = list.reduce(function (acc, th) { return acc + (Number(th.remote_knowledge_bases) || 0); }, 0);
      var h = publicNavHtml("themes");
      h += '<header class="obs-page-head"><div><h1>' + ui().icon("folder") + " <span>" + esc(t("kb.pub.themes_title")) + '</span></h1><p class="obs-subtitle claire-page-sub">' + esc(t("kb.pub.themes_subtitle")) + "</p></div>";
      h += '<dl class="obs-metrics">' +
        '<div class="obs-metric"><dt>' + esc(t("kb.pub.themes_metric_themes")) + "</dt><dd>" + esc(String(list.length)) + "</dd></div>" +
        '<div class="obs-metric"><dt>' + esc(t("kb.pub.themes_metric_local")) + "</dt><dd>" + esc(String(totalLocalKbs)) + "</dd></div>" +
        '<div class="obs-metric"><dt>' + esc(t("kb.pub.themes_metric_remote")) + "</dt><dd>" + esc(String(totalRemoteKbs)) + "</dd></div>" +
        "</dl></header>";
      h += '<div class="obs-catalog-toolbar">' + viewSwitchHtml("themes") + '<div class="claire-actions">' + knowledgeTypeChips("pub-themes-knowledge-type", knowledgeTypeFilter);
      if (bs.is_authenticated) h += '<button type="button" class="claire-btn pri" id="pub-th-create-kb">' + ui().icon("plus") + " " + esc(t("campaign.make_kb_link")) + "</button>";
      h += "</div></div>";
      if (loadError) h += '<div class="claire-note warn"><span>' + esc(ui().errorText(loadError)) + "</span></div>";
      h += '<div class="obs-themes-catalog-grid">';
      if (!list.length) {
        h += '<div class="claire-card"><p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p></div>";
      } else {
        list.forEach(function (th) {
          var entities = Array.isArray(th.top_entities) ? th.top_entities.slice(0, 4) : [];
          var peers = Array.isArray(th.peer_servers) ? th.peer_servers : [];
          h += '<article class="claire-card obs-catalog-card" data-knowledge-type="' + esc(th.knowledge_type || "real") + '"><div class="claire-card-head"><h2><a href="' + esc(themeHref(th.number)) + '">' + esc(th.label) + "</a></h2>" + knowledgeTypeTag(th.knowledge_type) + "</div>";
          if (th.description) h += '<p class="claire-hint">' + esc(th.description) + "</p>";
          if (entities.length) {
            h += '<div class="obs-catalog-entities">' + entities.map(function (name) { return '<span class="claire-tag">' + esc(name) + "</span>"; }).join("") + "</div>";
          }
          h += '<div class="claire-tags"><span class="claire-tag">' + esc(t("kb.pub.kb_count", { count: th.knowledge_bases || 0 })) + "</span>" +
            (th.remote_knowledge_bases ? '<span class="claire-tag">' + esc(t("kb.pub.remote_kb_count", { count: th.remote_knowledge_bases })) + "</span>" : "") + "</div>";
          if (peers.length) {
            h += '<div class="obs-catalog-peers" aria-label="' + esc(t("kb.pub.themes_peer_label")) + '">' +
              peers.map(function (domain) { return '<span class="obs-peer-pill">' + ui().icon("globe") + "<code>" + esc(domain) + "</code></span>"; }).join("") +
              "</div>";
          }
          h += "</article>";
        });
      }
      h += "</div>";
      host.innerHTML = h;
      ui().enhance(host);
      host.querySelectorAll('input[name="pub-themes-knowledge-type"]').forEach(function (inp) {
        inp.addEventListener("change", function () { knowledgeTypeFilter = inp.value; setKnowledgeTypeQuery(knowledgeTypeFilter); paint(); });
      });
      var createBtn = host.querySelector("#pub-th-create-kb");
      if (createBtn) createBtn.addEventListener("click", function () {
        dlgs().openCreateKbDialog({ defaultKnowledgeType: knowledgeTypeFilter === "fictional" ? "fictional" : "real", onCreated: function (kb) { if (kb && kb.number) window.location.assign(kbHref(kb.number)); } });
      });
    }
  }

  // GET /themes/{number}/: the theme with its knowledge bases: the shared ones of this server, the shared ones of other servers ("remote" true, with "server" and the origin link "url"; KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2 rule 3), then the attached public personal ones. "follows" true: the requester is a reviewer of a shared knowledge base of this server in the theme, or the admin, and each remote one has "follow" ("pending", "accepted", or null: this server does not follow it). Another reader gets no follow state.
  function renderThemeDetail(host, bs, ref) {
    ui().request(themeHref(ref)).then(function (r) {
      var h = publicNavHtml("themes");
      h += '<div class="claire-crumb"><a href="/themes">' + esc(t("kb.pub.theme_back")) + "</a><span>/</span><span>" + esc(ref) + "</span></div>";
      if (!r.ok || !r.data) {
        host.innerHTML = h + '<div class="claire-note warn"><span>' + esc(ui().errorText(r)) + "</span></div>";
        return;
      }
      var th = r.data;
      var kbs = th.knowledge_bases || [];
      var sharedKbs = kbs.filter(function (k) { return !k.is_personal && !k.remote; });
      var remoteKbs = kbs.filter(function (k) { return !!k.remote; });
      var personalKbs = kbs.filter(function (k) { return !!k.is_personal; });

      h += '<header class="obs-page-head"><div><h1>' + esc(th.label) + " " + knowledgeTypeTag(th.knowledge_type) + "</h1>" +
        (th.description ? '<p class="obs-subtitle claire-page-sub">' + esc(th.description) + "</p>" : "") +
        '<p class="obs-subtitle claire-page-sub">' + esc(t("themes.counts_federated", { knowledge_bases: th.shared_knowledge_bases || 0, remote: th.remote_knowledge_bases || 0, contributions: th.visible_contributions || 0 })) + "</p></div>";
      h += '<div class="obs-page-head-actions claire-actions">';
      if (bs.is_authenticated) {
        h += '<button type="button" class="claire-btn pri" id="th-contrib-btn">' + ui().icon("merge") + " " + esc(t("campaign.contribute_action")) + "</button>";
        h += '<button type="button" class="claire-btn sec" id="th-make-kb-btn">' + ui().icon("plus") + " " + esc(t("kb.pub.theme_make_kb")) + "</button>";
      } else {
        h += '<a class="claire-btn pri" href="' + esc(loginHref()) + '">' + esc(t("kb.pub.login_to_act")) + "</a>";
      }
      h += "</div></header>";

      var top = Array.isArray(th.top_entities) ? th.top_entities : [];
      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.theme_top_entities")) + "</h2></div>";
      h += top.length ? '<div class="claire-tags">' + top.map(function (name) { return '<span class="claire-tag">' + esc(name) + "</span>"; }).join("") + "</div>" : '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
      h += "</section>";

      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.theme_shared_kbs")) + "</h2></div>";
      h += ui().renderTable({
        caption: t("kb.pub.theme_shared_kbs"),
        columns: [
          { label: t("kb.pub.col_name"), render: function (k) { return '<a href="' + esc(kbHref(k.number)) + '"><strong>' + esc(k.label) + "</strong></a>"; } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (k) {
            return bs.is_authenticated ? '<button type="button" class="claire-btn claire-btn-sm" data-fork-kb="' + esc(k.number) + '" data-fork-lbl="' + esc(k.label) + '">' + ui().icon("fork") + " " + esc(t("kb.dlg.fork_confirm")) + "</button>" : "—";
          } }
        ],
        rows: sharedKbs
      });
      h += "</section>";

      // A knowledge base of another server has no page and no action on this server: the name is the link to its origin.
      if (remoteKbs.length) {
        h += '<section class="claire-card" id="th-remote-kbs"><div class="claire-card-head"><h2>' + esc(t("kb.pub.theme_remote_kbs")) + "</h2></div>";
        h += ui().renderTable({
          caption: t("kb.pub.theme_remote_kbs"),
          columns: [
            { label: t("kb.pub.col_name"), render: function (k) { return "<strong>" + remoteLink(k.url, k.label) + "</strong>"; } },
            { label: t("kb.pub.col_server"), render: function (k) { return esc(k.server); } }
          ].concat(th.follows ? [
            { label: t("kb.pub.col_follow"), render: function (k) { return '<span class="claire-tag' + (k.follow === "accepted" ? " ok" : k.follow === "pending" ? " warn" : "") + '" data-follow-state="' + esc(k.follow || "none") + '">' + esc(t("kb.pub.follow." + (k.follow || "none"))) + "</span>"; } }
          ] : []),
          rows: remoteKbs
        });
        h += "</section>";
      }

      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.theme_personal_kbs")) + "</h2></div>";
      if (!personalKbs.length) {
        h += '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
      } else {
        // A personal knowledge base has no "url" in the theme view: "/kb/<number>/" gets 308 to the path of its account.
        h += '<div class="claire-tags">' + personalKbs.map(function (k) {
          return '<a class="claire-tag" href="' + esc(kbHref(k.number)) + '">' + esc(k.label) + (k.limited ? ' <span class="claire-tag warn">' + esc(t("kb.pub.limited_badge")) + "</span>" : "") + "</a>";
        }).join("") + "</div>";
      }
      h += "</section>";

      host.innerHTML = h;
      ui().enhance(host);

      var makeBtn = host.querySelector("#th-make-kb-btn");
      if (makeBtn) makeBtn.addEventListener("click", function () {
        dlgs().openCreateKbDialog({ defaultKnowledgeType: th.knowledge_type, onCreated: function (kb) { if (kb && kb.number) window.location.assign(kbHref(kb.number)); } });
      });
      var contribBtn = host.querySelector("#th-contrib-btn");
      if (contribBtn) contribBtn.addEventListener("click", function () {
        dlgs().openContribPreview({ theme: th.number, themeLabel: th.label, homeHref: bs.home_href });
      });
      host.querySelectorAll("[data-fork-kb]").forEach(function (b) {
        b.addEventListener("click", function () {
          dlgs().openForkKbDialog({ kb: { number: b.getAttribute("data-fork-kb"), label: b.getAttribute("data-fork-lbl") }, onForked: function (kb) { if (kb && kb.number) window.location.assign(kbHref(kb.number)); } });
        });
      });
    });
  }

  // GET /kb: the root of the collection has each shared knowledge base in "knowledge_bases", with "stats" and with the "role" of the requester.
  function renderKbList(host, bs) {
    var knowledgeTypeFilter = knowledgeTypeFromQuery();
    var joinedOnly = false;
    var items = [];
    var loadError = null;

    function load() {
      ui().request("/kb").then(function (r) {
        items = (r.ok && r.data && r.data.knowledge_bases) || [];
        loadError = r.ok ? null : r;
        paint();
      });
    }

    function paint() {
      var visible = items.filter(function (k) {
        if (knowledgeTypeFilter !== "all" && k.knowledge_type !== knowledgeTypeFilter) return false;
        return !joinedOnly || !!k.role;
      });
      var h = publicNavHtml("kb");
      h += '<header class="obs-page-head"><div><h1>' + ui().icon("folder") + " <span>" + esc(t("kb.pub.kbs_title")) + '</span></h1><p class="obs-subtitle claire-page-sub">' + esc(t("kb.pub.kbs_subtitle")) + "</p></div></header>";
      h += '<div class="obs-catalog-toolbar">' + viewSwitchHtml("kb") + '<div class="claire-actions">' + knowledgeTypeChips("pub-kb-knowledge-type", knowledgeTypeFilter);
      if (bs.is_authenticated) {
        h += '<label class="claire-chip"><input type="checkbox" id="pub-kb-joined"' + (joinedOnly ? " checked" : "") + "/><span>" + esc(t("kb.pub.filter_joined")) + "</span></label>";
        h += '<button type="button" class="claire-btn pri" id="pub-kb-make">' + ui().icon("plus") + " " + esc(t("campaign.make_kb_link")) + "</button>";
      }
      h += "</div></div>";
      if (loadError) h += '<div class="claire-note warn"><span>' + esc(ui().errorText(loadError)) + "</span></div>";

      h += '<section class="claire-card">';
      h += ui().renderTable({
        caption: t("kb.pub.kbs_title"),
        columns: [
          { label: t("kb.pub.col_name"), render: function (k) {
            return '<a href="' + esc(kbHref(k.number)) + '"><strong>' + esc(k.label) + "</strong></a> " + knowledgeTypeTag(k.knowledge_type) +
              (k.state === "archived" ? ' <span class="claire-tag warn">' + esc(dlgs().stateLabel("archived")) + "</span>" : "") +
              (k.reembed_required ? ' <span class="claire-tag danger">' + esc(t("kb.pub.reembed_badge")) + "</span>" : "") +
              (k.description ? '<br><span class="claire-hint">' + esc(k.description) + "</span>" : "");
          } },
          { label: t("kb.pub.col_policy"), render: function (k) { return esc(t("kb.join_policy." + k.join_policy)); } },
          { label: t("kb.pub.col_role"), render: function (k) {
            if (k.membership_state === "pending" || k.membership_state === "banned") return '<span class="claire-tag ' + (k.membership_state === "banned" ? "danger" : "warn") + '">' + esc(t("kb.member_state." + k.membership_state)) + "</span>";
            return k.role ? '<span class="claire-tag ok">' + esc(t("kb.role." + k.role)) + "</span>" : "—";
          } },
          { label: t("kb.pub.col_stats"), render: function (k) { return esc((k.stats && k.stats.documents) || 0) + " / " + esc((k.stats && k.stats.entities) || 0); } }
        ],
        rows: visible
      });
      h += "</section>";

      host.innerHTML = h;
      ui().enhance(host);
      host.querySelectorAll('input[name="pub-kb-knowledge-type"]').forEach(function (inp) {
        inp.addEventListener("change", function () { knowledgeTypeFilter = inp.value; setKnowledgeTypeQuery(knowledgeTypeFilter); paint(); });
      });
      var joinedBox = host.querySelector("#pub-kb-joined");
      if (joinedBox) joinedBox.addEventListener("change", function () { joinedOnly = joinedBox.checked; paint(); });
      var makeBtn = host.querySelector("#pub-kb-make");
      if (makeBtn) makeBtn.addEventListener("click", function () {
        dlgs().openCreateKbDialog({ defaultKnowledgeType: knowledgeTypeFilter === "fictional" ? "fictional" : "real", onCreated: function () { load(); } });
      });
    }

    load();
  }

  // The page of one shared knowledge base. The data: GET /kb/{number}/ as JSON ({"knowledge_base": {...}} with "role" and "membership_state" of the requester), GET /kb/{number}/members, and GET /kb/{number}/contributions (each contribution has its "title").
  function renderKbDetail(host, bs, kbNum) {
    function load() {
      Promise.all([
        ui().request(kbHref(kbNum)),
        ui().request(kbHref(kbNum) + "contributions"),
        ui().request(kbHref(kbNum) + "members")
      ]).then(function (all) {
        var kb = (all[0].ok && all[0].data && all[0].data.knowledge_base) || null;
        var h = publicNavHtml("kb");
        h += '<div class="claire-crumb"><a href="/kb">' + esc(t("kb.pub.nav_kbs")) + "</a><span>/</span><span>" + esc(kbNum) + "</span></div>";
        if (!kb) {
          host.innerHTML = h + '<div class="claire-note warn"><span>' + esc(all[0].ok ? t("kb.err.kb_not_found") : ui().errorText(all[0])) + "</span></div>";
          return;
        }
        var contributions = (all[1].ok && all[1].data && all[1].data.contributions) || [];
        var members = (all[2].ok && all[2].data && all[2].data.members) || [];
        // "membership_state" keeps a join request and a ban after a reload; "role" is the role of an active membership.
        var role = dlgs().membershipOf(kb).role;
        var archived = kb.state === "archived";

        h += '<section class="claire-card"><header class="obs-page-head"><div><h1>' + esc(kb.label) + " " + knowledgeTypeTag(kb.knowledge_type) +
          ' <span class="claire-tag">' + esc(t("kb.join_policy." + kb.join_policy)) + "</span>" +
          (role ? ' <span class="claire-tag ok">' + esc(t("kb.role." + role)) + "</span>" : "") +
          (archived ? ' <span class="claire-tag warn">' + esc(dlgs().stateLabel("archived")) + "</span>" : "") +
          "</h1>" + (kb.description ? '<p class="obs-subtitle claire-page-sub">' + esc(kb.description) + "</p>" : "") +
          '<p class="obs-subtitle claire-page-sub">' + esc(t("kb.dlg.col_docs")) + ": " + esc((kb.stats && kb.stats.documents) || 0) + " · " + esc(t("kb.dlg.col_entities")) + ": " + esc((kb.stats && kb.stats.entities) || 0) + "</p></div>";
        h += '<div class="obs-page-head-actions claire-actions">';
        if (bs.is_authenticated) h += dlgs().kbActionsHtml(kb, { isAdmin: !!bs.is_admin });
        else h += '<a class="claire-btn pri" href="' + esc(loginHref()) + '">' + esc(t("kb.pub.login_to_act")) + "</a>";
        h += "</div></header>";
        if (bs.is_authenticated) h += dlgs().kbMembershipNoteHtml(kb);
        h += '<div class="claire-note"><span>' + esc(t("kb.pub.kb_ingest_blocked_desc")) + "</span></div></section>";

        h += '<div class="claire-split"><div class="claire-list-col">';
        h += '<section class="claire-card"><div class="claire-card-head"><h3>' + esc(t("kb.pub.kb_members_roles")) + "</h3></div>";
        h += members.length ? members.map(function (m) {
          return '<div class="claire-list-item-row" style="font-size:12.5px"><span>' + (m.username ? '<a href="/@' + esc(encodeURIComponent(m.username)) + '/">' + esc(m.display_name || m.username) + "</a>" : esc(m.display_name || m.account)) + '</span><span class="claire-tag">' + esc(t(m.state === "active" ? "kb.role." + m.role : "kb.member_state." + m.state)) + "</span></div>";
        }).join("") : '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
        h += "</section></div>";

        h += '<div class="claire-detail-col"><section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.kb_docs")) + "</h2></div>";
        h += ui().renderTable({
          caption: t("kb.pub.kb_docs"),
          columns: [
            { label: t("kb.dlg.col_document"), render: function (c) {
              var title = esc(c.title || c.document || "—");
              return (c.state === "visible" ? '<a href="' + esc(kbHref(kbNum) + "?doc=" + encodeURIComponent(c.document)) + '"><strong>' + title + "</strong></a>" : "<strong>" + title + "</strong>") +
                (c.state !== "visible" ? ' <span class="claire-tag warn">' + esc(dlgs().stateLabel(c.state)) + "</span>" : "");
            } },
            // The contributor: an account of this server, or the actor of an account of another server (the credit of a copy, federation design Section 10.1), as on the review page. Only an account that is gone is "deleted account".
            { label: t("kb.dlg.col_contributor"), render: function (c) { return esc((c.contributor && (c.contributor.display_name || c.contributor.account)) || (c.remote && c.remote.credit) || t("kb.pub.contributor_gone")); } },
            { label: t("kb.pub.col_origin"), render: function (c) { return esc(t("kb.origin." + c.origin)); } }
          ],
          rows: contributions
        });
        h += "</section></div></div>";

        host.innerHTML = h;
        ui().enhance(host);

        dlgs().bindKbActions(host, kb, {
          homeHref: bs.home_href,
          onChanged: function () { load(); },
          onForked: function (fork) { if (fork && fork.number) window.location.assign(kbHref(fork.number)); }
        });
      });
    }

    load();
  }

  function boot() {
    var homeShowcase = document.getElementById("public-home-showcase");
    if (homeShowcase) renderHomeShowcase(homeShowcase);
    var homeRecent = document.getElementById("public-home-recent");
    if (homeRecent) renderHomeRecent(homeRecent);
    var rootEl = document.getElementById("public-kb-root");
    if (!rootEl) return;
    var bs = parseBootstrap() || {};
    if (bs.view === "theme_detail" && bs.ref) renderThemeDetail(rootEl, bs, bs.ref);
    else if (bs.view === "themes_list") renderThemesList(rootEl, bs);
    else if (bs.view === "kb_detail" && bs.ref) renderKbDetail(rootEl, bs, bs.ref);
    else if (bs.view === "kb_list") renderKbList(rootEl, bs);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window);
