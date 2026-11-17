/* Public views for /, /themes, /themes/{number}, /kb, and /kb/{number}/ (Items 1, 2, 3). */
(function (root) {
  "use strict";

  function ui() { return root.ClaireUi; }
  function dlgs() { return root.ClaireKbDialogs; }
  function t(key, params, fallback) { return ui().t(key, params, fallback); }
  function esc(v) { return ui().esc(v); }

  function parseBootstrap() {
    var el = document.getElementById("claire-public-bootstrap");
    if (!el) return null;
    try { return JSON.parse(el.textContent || "{}"); } catch (_) { return null; }
  }

  function themeHref(num) { return "/explore/themes/" + encodeURIComponent(num); }

  function publicNavHtml(cur) {
    var links = [
      { id: "home", href: "/", label: t("kb.pub.nav_home", null, "Home") },
      { id: "themes", href: "/themes", label: t("kb.pub.nav_themes", null, "Themes") },
      { id: "kb", href: "/kb", label: t("kb.pub.nav_kbs", null, "Shared KBs") },
      { id: "accounts", href: "/explore/accounts", label: t("kb.pub.nav_accounts", null, "Accounts") }
    ];
    return '<nav class="claire-pubnav" aria-label="Public discovery">' + links.map(function (it) {
      return '<a href="' + esc(it.href) + '"' + (it.id === cur ? ' aria-current="page"' : "") + ">" + esc(it.label) + "</a>";
    }).join("") + "</nav>";
  }

  function normalizeThemes(raw) {
    if (!raw) return [];
    if (Array.isArray(raw)) return raw;
    if (Array.isArray(raw.themes)) return raw.themes;
    if (Array.isArray(raw.orderedItems)) return raw.orderedItems;
    if (Array.isArray(raw.items)) return raw.items;
    return [];
  }

  function renderHomeShowcase(host) {
    var typeFilter = "all";
    var themes = [];
    var activity = [];
    var servers = [];

    Promise.all([
      ui().request("/themes?page=true"),
      ui().request("/api/federation/timeline"),
      ui().request("/api/admin/federation/known-servers")
    ]).then(function (all) {
      themes = normalizeThemes(all[0].ok && all[0].data);
      activity = (all[1].ok && all[1].data && (all[1].data.orderedItems || all[1].data.items)) || [];
      servers = (all[2].ok && all[2].data && (all[2].data.servers || all[2].data.items)) || [];
      paint();
    });

    function paint() {
      var visThemes = themes.filter(function (th) {
        if (typeFilter === "all") return true;
        return (th.knowledge_type || "real") === typeFilter;
      });
      var h = '<div class="claire-panel-inner" style="margin-bottom:1.5rem">';
      h += publicNavHtml("home");
      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + ui().icon("folder") + " " + esc(t("kb.pub.home_themes_title", null, "Server Themes")) + "</h2>";
      h += '<div class="claire-actions">';
      h += ui().renderChips({
        name: "home-theme-type",
        label: t("kb.pub.filter_type_aria", null, "Filter by knowledge type"),
        value: typeFilter,
        options: [
          { value: "all", label: t("kb.pub.filter_all", null, "All") },
          { value: "real", label: t("kb.pub.filter_real", null, "Real-world") },
          { value: "fictional", label: t("kb.pub.filter_fictional", null, "Fictional") }
        ]
      });
      h += '<button type="button" class="claire-btn pri claire-btn-sm" id="pub-home-create-kb">' + ui().icon("plus") + " " + esc(t("kb.dlg.create_btn", null, "Create Shared KB")) + "</button>";
      h += "</div></div>";
      if (!visThemes.length) {
        h += '<p class="claire-hint">' + esc(t("kb.ui.empty_list", null, "No items yet.")) + "</p>";
      } else {
        h += '<div class="claire-grid-3">';
        visThemes.slice(0, 6).forEach(function (th) {
          var num = th.number != null ? th.number : th.id;
          h += '<a class="claire-card" style="text-decoration:none;color:inherit" href="' + esc(themeHref(num)) + '">' +
            '<div class="claire-card-head"><strong>' + esc(th.label) + '</strong><span class="claire-tag ' + (th.knowledge_type === "fictional" ? "fic" : "acc") + '">' + esc(th.knowledge_type || "real") + "</span></div>" +
            '<span class="claire-hint">' + esc(th.description || "") + "</span>" +
            '<div class="claire-tags"><span class="claire-tag">' + esc(th.shared_knowledge_bases != null ? th.shared_knowledge_bases : 0) + " KBs</span>" +
            '<span class="claire-tag">' + esc(th.visible_contributions != null ? th.visible_contributions : (th.documents || 0)) + " docs</span></div></a>";
        });
        h += "</div>";
      }
      h += "</section>";

      h += '<div class="claire-grid-2">';
      h += '<section class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("pulse") + " " + esc(t("kb.pub.home_activity_title", null, "Cross-server recent activity")) + "</h3></div>";
      h += activity.length ? ("<ul style=\"margin:0;padding-left:18px;font-size:12.5px\">" + activity.slice(0, 5).map(function (a) { return "<li>" + esc(a.title || a.summary || a.id) + "</li>"; }).join("") + "</ul>") : ('<p class="claire-hint">' + esc(t("kb.pub.activity_empty", null, "No cross-server activity recorded yet.")) + "</p>");
      h += "</section>";
      h += '<section class="claire-card"><div class="claire-card-head"><h3>' + ui().icon("globe") + " " + esc(t("kb.pub.home_servers_title", null, "Federated servers")) + "</h3></div>";
      h += servers.length ? ("<div class=\"claire-tags\">" + servers.slice(0, 8).map(function (s) { return '<span class="claire-tag rem">' + esc(s.domain) + "</span>"; }).join("") + "</div>") : ('<p class="claire-hint">' + esc(t("kb.pub.servers_empty", null, "No known federated servers discovered yet.")) + "</p>");
      h += "</section></div></div>";

      host.innerHTML = h;
      ui().enhance(host);
      host.querySelectorAll('input[name="home-theme-type"]').forEach(function (inp) {
        inp.addEventListener("change", function () { typeFilter = inp.value; paint(); });
      });
      var cBtn = host.querySelector("#pub-home-create-kb");
      if (cBtn && dlgs()) cBtn.addEventListener("click", function () { dlgs().openCreateKbDialog({}); });
    }
  }

  function renderThemesList(host, bs) {
    var typeFilter = new URLSearchParams(window.location.search).get("type") || "all";
    var urlTheme = new URLSearchParams(window.location.search).get("id");
    if (urlTheme) {
      renderThemeDetail(host, bs, urlTheme);
      return;
    }
    var themes = [];
    ui().request("/themes?page=true").then(function (r) {
      themes = normalizeThemes(r.ok && r.data);
      paint(r);
    });

    function paint(res) {
      var list = themes.filter(function (th) { return typeFilter === "all" || (th.knowledge_type || "real") === typeFilter; });
      var h = publicNavHtml("themes");
      h += '<div class="claire-page-head"><div><h1>' + ui().icon("folder") + " " + esc(t("kb.pub.themes_title", null, "Public Themes")) + '</h1><p class="claire-page-sub">' + esc(t("kb.pub.themes_subtitle")) + "</p></div>";
      h += '<div class="claire-actions">';
      h += ui().renderChips({
        name: "pub-themes-type",
        label: t("kb.pub.filter_type_aria"),
        value: typeFilter,
        options: [
          { value: "all", label: t("kb.pub.filter_all") },
          { value: "real", label: t("kb.pub.filter_real") },
          { value: "fictional", label: t("kb.pub.filter_fictional") }
        ]
      });
      if (bs.is_authenticated) {
        h += '<button type="button" class="claire-btn pri" id="pub-th-create-kb">' + ui().icon("plus") + " " + esc(t("kb.dlg.create_btn")) + "</button>";
      }
      h += "</div></div>";
      if (res && res.unavailable) h += '<div class="claire-note warn"><span>' + esc(t("kb.dlg.backend_pending")) + "</span></div>";
      h += '<div class="claire-grid-3">';
      if (!list.length) {
        h += '<div class="claire-card"><p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p></div>";
      } else {
        list.forEach(function (th) {
          var num = th.number != null ? th.number : th.id;
          var top = Array.isArray(th.top_entities) ? th.top_entities.slice(0, 4) : [];
          h += '<article class="claire-card"><div class="claire-card-head"><h2><a href="' + esc(themeHref(num)) + '">' + esc(th.label) + '</a></h2><span class="claire-tag ' + (th.knowledge_type === "fictional" ? "fic" : "acc") + '">' + esc(th.knowledge_type || "real") + "</span></div>";
          if (th.description) h += '<p class="claire-hint">' + esc(th.description) + "</p>";
          if (top.length) h += '<div class="claire-tags">' + top.map(function (e) { return '<span class="claire-tag">' + esc(typeof e === "string" ? e : e.label) + "</span>"; }).join("") + "</div>";
          h += '<div class="claire-tags"><span class="claire-tag">' + esc(th.shared_knowledge_bases != null ? th.shared_knowledge_bases : 0) + " KBs</span></div></article>";
        });
      }
      h += "</div>";
      host.innerHTML = h;
      ui().enhance(host);
      host.querySelectorAll('input[name="pub-themes-type"]').forEach(function (inp) {
        inp.addEventListener("change", function () { typeFilter = inp.value; paint(res); });
      });
      var cb = host.querySelector("#pub-th-create-kb");
      if (cb && dlgs()) cb.addEventListener("click", function () { dlgs().openCreateKbDialog({ defaultType: typeFilter === "fictional" ? "fictional" : "real" }); });
    }
  }

  function renderThemeDetail(host, bs, ref) {
    ui().request("/themes/" + encodeURIComponent(ref) + "/").then(function (r) {
      var th = (r.ok && r.data && (r.data.theme || r.data)) || { number: ref, label: "Theme #" + ref, knowledge_type: "real", top_entities: [], knowledge_bases: [] };
      var kbs = th.knowledge_bases || [];
      var sharedKbs = kbs.filter(function (k) { return !k.is_personal; });
      var personalKbs = kbs.filter(function (k) { return !!k.is_personal; });

      var h = publicNavHtml("themes");
      h += '<div class="claire-crumb"><a href="/themes">' + esc(t("kb.pub.theme_back")) + "</a><span>/</span><span>#" + esc(th.number || ref) + "</span></div>";
      h += '<div class="claire-page-head"><div><h1>' + esc(th.label || ("Theme #" + ref)) + ' <span class="claire-tag ' + (th.knowledge_type === "fictional" ? "fic" : "acc") + '">' + esc(th.knowledge_type || "real") + "</span></h1>" +
        (th.description ? '<p class="claire-page-sub">' + esc(th.description) + "</p>" : "") + "</div>";
      h += '<div class="claire-actions">';
      if (bs.is_authenticated) {
        h += '<button type="button" class="claire-btn pri" id="th-contrib-btn">' + ui().icon("merge") + " " + esc(t("kb.pub.theme_contribute")) + "</button>";
        h += '<button type="button" class="claire-btn sec" id="th-make-kb-btn">' + ui().icon("plus") + " " + esc(t("kb.pub.theme_make_kb")) + "</button>";
      } else {
        h += '<a class="claire-btn pri" href="/login">' + esc(t("kb.pub.login_to_act")) + "</a>";
      }
      h += "</div></div>";

      if (r.unavailable) h += '<div class="claire-note warn"><span>' + esc(t("kb.dlg.backend_pending")) + "</span></div>";

      var top = th.top_entities || [];
      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.theme_top_entities")) + "</h2></div>";
      h += top.length ? '<div class="claire-tags">' + top.map(function (e) { return '<span class="claire-tag">' + esc(typeof e === "string" ? e : e.label) + "</span>"; }).join("") + "</div>" : '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
      h += "</section>";

      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.theme_shared_kbs")) + "</h2></div>";
      h += ui().renderTable({
        caption: t("kb.pub.theme_shared_kbs"),
        columns: [
          { label: t("kb.pub.col_name"), render: function (k) { return '<a href="/kb/' + esc(k.number) + '/"><strong>' + esc(k.label) + "</strong></a>"; } },
          { label: t("kb.pub.col_origin"), render: function (k) { return '<span class="claire-tag ' + (k.origin ? "rem" : "acc") + '">' + esc(k.origin ? t("kb.pub.origin_remote") : t("kb.pub.origin_local")) + "</span>"; } },
          { label: t("kb.pub.col_actions"), actions: true, render: function (k) {
            var btns = "";
            if (!k.origin && bs.is_authenticated) btns += '<button type="button" class="claire-btn claire-btn-sm" data-fork-kb="' + esc(k.number) + '" data-fork-lbl="' + esc(k.label) + '">' + ui().icon("fork") + " Fork</button> ";
            if (k.origin && k.url) btns += '<a class="claire-btn claire-btn-sm" href="' + esc(k.url) + '" target="_blank" rel="noopener">' + esc(t("kb.pub.open_origin")) + "</a>";
            return btns || "—";
          } }
        ],
        rows: sharedKbs
      });
      h += "</section>";

      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.theme_personal_kbs")) + "</h2></div>";
      if (!personalKbs.length) {
        h += '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
      } else {
        h += '<div class="claire-tags">' + personalKbs.map(function (k) {
          return '<a class="claire-tag" href="' + esc(k.url || ("/kb/" + k.number + "/")) + '">' + esc(k.label) + (k.limited ? ' <span class="claire-tag warn">' + esc(t("kb.pub.limited_badge")) + "</span>" : "") + "</a>";
        }).join("") + "</div>";
      }
      h += "</section>";

      host.innerHTML = h;
      ui().enhance(host);

      var mkBtn = host.querySelector("#th-make-kb-btn");
      if (mkBtn && dlgs()) mkBtn.addEventListener("click", function () { dlgs().openCreateKbDialog({ defaultType: th.knowledge_type || "real", defaultTheme: ref }); });
      var cBtn = host.querySelector("#th-contrib-btn");
      if (cBtn && dlgs()) cBtn.addEventListener("click", function () { dlgs().openNoThemePicker({ documentId: "" }); });
      host.querySelectorAll("[data-fork-kb]").forEach(function (b) {
        b.addEventListener("click", function () {
          dlgs().openForkKbDialog({ kb: { number: b.getAttribute("data-fork-kb"), label: b.getAttribute("data-fork-lbl") } });
        });
      });
    });
  }

  function renderKbList(host, bs) {
    var typeFilter = "all";
    var joinedOnly = false;
    var items = [];
    ui().request("/kb?page=true&limit=60").then(function (r) {
      items = (r.ok && r.data && (r.data.orderedItems || r.data.items)) || [];
      paint(r);
    });

    function paint(res) {
      var vis = items.filter(function (k) {
        if (k.is_personal) return false;
        if (typeFilter !== "all" && (k.knowledge_type || "real") !== typeFilter) return false;
        if (joinedOnly && !k.role) return false;
        return true;
      });
      var local = vis.filter(function (k) { return !k.origin; });
      var remote = vis.filter(function (k) { return !!k.origin; });

      var h = publicNavHtml("kb");
      h += '<div class="claire-page-head"><div><h1>' + ui().icon("folder") + " " + esc(t("kb.pub.kbs_title")) + '</h1><p class="claire-page-sub">' + esc(t("kb.pub.kbs_subtitle")) + "</p></div>";
      h += '<div class="claire-actions">';
      h += ui().renderChips({
        name: "pub-kb-type",
        label: t("kb.pub.filter_type_aria"),
        value: typeFilter,
        options: [
          { value: "all", label: t("kb.pub.filter_all") },
          { value: "real", label: t("kb.pub.filter_real") },
          { value: "fictional", label: t("kb.pub.filter_fictional") }
        ]
      });
      if (bs.is_authenticated) {
        h += '<label class="claire-chip"><input type="checkbox" id="pub-kb-joined"' + (joinedOnly ? " checked" : "") + "/><span>" + esc(t("kb.pub.filter_joined")) + "</span></label>";
        h += '<button type="button" class="claire-btn pri" id="pub-kb-make">' + ui().icon("plus") + " " + esc(t("kb.dlg.create_btn")) + "</button>";
      }
      h += "</div></div>";

      if (res && res.unavailable) h += '<div class="claire-note warn"><span>' + esc(t("kb.dlg.backend_pending")) + "</span></div>";

      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.local_kbs")) + "</h2></div>";
      h += ui().renderTable({
        caption: t("kb.pub.local_kbs"),
        columns: [
          { label: t("kb.pub.col_name"), render: function (k) {
            return '<a href="/kb/' + esc(k.number) + '/"><strong>' + esc(k.label) + '</strong></a> <span class="claire-tag ' + (k.knowledge_type === "fictional" ? "fic" : "acc") + '">' + esc(k.knowledge_type || "real") + "</span>" +
              (k.reembed_required ? ' <span class="claire-tag danger">' + esc(t("kb.pub.reembed_badge")) + "</span>" : "");
          } },
          { label: t("kb.pub.col_policy"), render: function (k) { return esc(k.join_policy || "open"); } },
          { label: t("kb.pub.col_mods"), render: function (k) {
            var m = k.active_moderators != null ? k.active_moderators : 1;
            return m === 0 ? '<span class="claire-tag warn">' + esc(t("kb.pub.no_mod_warn")) + "</span>" : esc(m);
          } },
          { label: t("kb.pub.col_role"), render: function (k) { return k.role ? '<span class="claire-tag ok">' + esc(k.role) + "</span>" : "—"; } },
          { label: t("kb.pub.col_stats"), render: function (k) { return esc((k.stats && k.stats.documents) || 0) + " / " + esc((k.stats && k.stats.entities) || 0); } }
        ],
        rows: local
      });
      h += "</section>";

      h += '<section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.remote_kbs")) + "</h2></div>";
      h += ui().renderTable({
        caption: t("kb.pub.remote_kbs"),
        columns: [
          { label: t("kb.pub.col_name"), render: function (k) { return '<a href="/kb/' + esc(k.number) + '/"><strong>' + esc(k.label) + "</strong></a>"; } },
          { label: t("kb.pub.col_origin"), render: function (k) { return '<span class="claire-tag rem">' + esc(k.origin) + "</span>"; } },
          { label: t("kb.pub.col_stats"), render: function (k) { return esc((k.stats && k.stats.documents) || 0); } }
        ],
        rows: remote
      });
      h += "</section>";

      host.innerHTML = h;
      ui().enhance(host);
      host.querySelectorAll('input[name="pub-kb-type"]').forEach(function (inp) {
        inp.addEventListener("change", function () { typeFilter = inp.value; paint(res); });
      });
      var jcb = host.querySelector("#pub-kb-joined");
      if (jcb) jcb.addEventListener("change", function () { joinedOnly = jcb.checked; paint(res); });
      var mkb = host.querySelector("#pub-kb-make");
      if (mkb && dlgs()) mkb.addEventListener("click", function () { dlgs().openCreateKbDialog({ defaultType: typeFilter === "fictional" ? "fictional" : "real" }); });
    }
  }

  function renderKbDetail(host, bs, kbNum) {
    Promise.all([
      ui().request("/kb/" + encodeURIComponent(kbNum) + "/"),
      ui().request("/kb/" + encodeURIComponent(kbNum) + "/contributions"),
      ui().request("/kb/" + encodeURIComponent(kbNum) + "/members")
    ]).then(function (all) {
      var kbRes = all[0], cRes = all[1], mRes = all[2];
      var kb = (kbRes.ok && kbRes.data && (kbRes.data.knowledge_base || kbRes.data)) || { number: kbNum, label: "Shared KB #" + kbNum, knowledge_type: "real", join_policy: "open", stats: {} };
      var contribs = (cRes.ok && cRes.data && (cRes.data.contributions || cRes.data.orderedItems)) || [];
      var members = (mRes.ok && mRes.data && (mRes.data.members || mRes.data.orderedItems)) || [];

      var h = publicNavHtml("kb");
      h += '<div class="claire-crumb"><a href="/kb">' + esc(t("kb.pub.nav_kbs")) + "</a><span>/</span><span>#" + esc(kbNum) + "</span></div>";

      if (kbRes.unavailable) {
        h += '<div class="claire-note warn"><span>' + esc(t("kb.dlg.backend_pending")) + "</span></div>";
      }

      if (kb.origin) {
        // Remote shared KB record view
        h += '<section class="claire-card"><div class="claire-page-head"><div><h1>' + esc(kb.label) + ' <span class="claire-tag rem">' + esc(t("kb.pub.remote_record_title")) + "</span></h1>" +
          '<p class="claire-page-sub">' + esc(kb.origin) + "</p></div>";
        h += '<div class="claire-actions">';
        if (bs.is_authenticated) {
          h += '<button type="button" class="claire-btn pri" id="kb-remote-contrib">' + esc(t("kb.pub.kb_direct_contribute")) + "</button>";
        }
        h += '<a class="claire-btn" href="' + esc(kb.origin_url || kb.origin) + '" target="_blank" rel="noopener">' + esc(t("kb.pub.open_origin")) + "</a>";
        h += "</div></div>";
        h += "<dl style=\"display:grid;grid-template-columns:160px 1fr;gap:6px 12px;font-size:13px\">" +
          "<dt>" + esc(t("kb.pub.remote_actor")) + "</dt><dd><code class=\"claire-mono\">" + esc(kb.actor_iri || kb.origin) + "</code></dd>" +
          "<dt>" + esc(t("kb.dlg.col_docs")) + "</dt><dd>" + esc((kb.stats && kb.stats.documents) || 0) + "</dd>" +
          "<dt>" + esc(t("kb.pub.remote_local_theme")) + "</dt><dd>" + (kb.theme_number ? '<a href="' + esc(themeHref(kb.theme_number)) + '">' + esc(kb.theme_label || ("#" + kb.theme_number)) + "</a>" : "—") + "</dd>" +
          "<dt>" + esc(t("kb.pub.remote_follow_state")) + "</dt><dd>" + esc(kb.follow_state || "following") + "</dd></dl></section>";
        host.innerHTML = h;
        ui().enhance(host);
        return;
      }

      // Local Shared KB workspace header + permissions
      var role = kb.role || (bs.is_authenticated ? "none" : "public");
      var canManage = role === "moderator" || role === "collaborator";
      h += '<section class="claire-card"><div class="claire-page-head"><div><h1>' + esc(kb.label || ("#" + kbNum)) +
        ' <span class="claire-tag ' + (kb.knowledge_type === "fictional" ? "fic" : "acc") + '">' + esc(kb.knowledge_type || "real") + "</span>" +
        ' <span class="claire-tag">' + esc(kb.join_policy || "open") + "</span>" +
        (role !== "public" && role !== "none" ? ' <span class="claire-tag ok">' + esc(role) + "</span>" : "") +
        "</h1>" + (kb.description ? '<p class="claire-page-sub">' + esc(kb.description) + "</p>" : "") + "</div>";

      h += '<div class="claire-actions">';
      if (bs.is_authenticated) {
        if (role === "none") h += '<button type="button" class="claire-btn pri" id="kb-join-btn">' + esc(t("kb.pub.kb_join")) + "</button>";
        else h += '<button type="button" class="claire-btn sec" id="kb-leave-btn">' + esc(t("kb.pub.kb_leave")) + "</button>";
        h += '<button type="button" class="claire-btn pri" id="kb-direct-contrib-btn">' + ui().icon("merge") + " " + esc(t("kb.pub.kb_direct_contribute")) + "</button>";
        h += '<button type="button" class="claire-btn" id="kb-fork-btn">' + ui().icon("fork") + " Fork</button>";
        h += '<button type="button" class="claire-btn" disabled title="' + esc(t("kb.pub.kb_ingest_blocked_desc")) + '">' + ui().icon("plus") + " " + esc(t("kb.pub.kb_ingest_blocked_title")) + "</button>";
        if (canManage) h += '<a class="claire-btn sec" href="/settings/kb/' + esc(kbNum) + '">' + esc(t("kb.pub.kb_review_manage")) + "</a>";
      } else {
        h += '<a class="claire-btn pri" href="/login">' + esc(t("kb.pub.login_to_act")) + "</a>";
      }
      if (kb.theme_number) h += '<a class="claire-btn" href="' + esc(themeHref(kb.theme_number)) + '">' + esc(kb.theme_label || t("kb.pub.nav_themes")) + "</a>";
      h += "</div></div>";
      h += '<div class="claire-note warn"><span>' + esc(t("kb.pub.kb_ingest_blocked_desc")) + "</span></div></section>";

      h += '<div class="claire-split"><div class="claire-list-col">';
      h += '<section class="claire-card"><div class="claire-card-head"><h3>' + esc(t("kb.pub.kb_members_roles")) + "</h3></div>";
      h += members.length ? members.map(function (m) {
        return '<div class="claire-list-item-row" style="font-size:12.5px"><span>' + esc(m.display_name || m.account) + (m.grant_expires_at ? ' <span class="claire-tag rem">Grant</span>' : "") + '</span><span class="claire-tag">' + esc(m.role) + "</span></div>";
      }).join("") : '<p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p>";
      h += "</section>";

      h += '<details class="claire-card"><summary style="cursor:pointer;font-weight:600;font-size:13px">' + esc(t("kb.pub.kb_permissions")) + " (" + esc(role) + ")</summary>";
      h += ui().renderTable({
        caption: t("kb.pub.kb_permissions"),
        columns: [
          { label: "Capability", key: "cap" },
          { label: "Allowed", render: function (p) { return '<span class="claire-tag ' + (p.ok ? "ok" : "danger") + '">' + esc(p.ok ? "Yes" : "403") + "</span>"; } }
        ],
        rows: [
          { cap: "Read shared graph & docs", ok: true },
          { cap: "Submit contribution", ok: bs.is_authenticated },
          { cap: "Direct ingest", ok: false },
          { cap: "Review & curate", ok: role === "collaborator" || role === "moderator" },
          { cap: "Moderator settings & grants", ok: role === "moderator" }
        ]
      });
      h += "</details></div>";

      h += '<div class="claire-detail-col"><section class="claire-card"><div class="claire-card-head"><h2>' + esc(t("kb.pub.kb_docs")) + "</h2></div>";
      h += ui().renderTable({
        caption: t("kb.pub.kb_docs"),
        columns: [
          { label: t("kb.dlg.col_docs"), render: function (c) {
            return "<strong>" + esc(c.title || c.document) + "</strong>" +
              (c.remote_deleted ? ' <span class="claire-tag warn">' + esc(t("kb.pub.doc_remote_deleted")) + "</span>" : "") +
              (c.state === "hidden" ? ' <span class="claire-tag warn">' + esc(t("kb.pub.doc_hidden")) + "</span>" : "");
          } },
          { label: "Contributor", render: function (c) { return esc((c.contributor && (c.contributor.display_name || c.contributor.account)) || "deleted account"); } },
          { label: t("kb.pub.col_origin"), render: function (c) { return '<span class="claire-tag ' + (c.origin === "remote" ? "rem" : "acc") + '">' + esc(c.origin === "remote" ? t("kb.pub.origin_remote") : t("kb.pub.origin_local")) + "</span>"; } }
        ],
        rows: contribs
      });
      h += "</section></div></div>";

      host.innerHTML = h;
      ui().enhance(host);

      var joinBtn = host.querySelector("#kb-join-btn");
      if (joinBtn) joinBtn.addEventListener("click", function () {
        ui().request("/kb/" + encodeURIComponent(kbNum) + "/membership", { method: "POST", body: {} }).then(function (r) {
          if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
          renderKbDetail(host, bs, kbNum);
        });
      });
      var leaveBtn = host.querySelector("#kb-leave-btn");
      if (leaveBtn) leaveBtn.addEventListener("click", function () {
        ui().request("/kb/" + encodeURIComponent(kbNum) + "/membership", { method: "DELETE" }).then(function (r) {
          if (!r.ok) { ui().toast(r.error || "Failed", "danger"); return; }
          renderKbDetail(host, bs, kbNum);
        });
      });
      var forkBtn = host.querySelector("#kb-fork-btn");
      if (forkBtn && dlgs()) forkBtn.addEventListener("click", function () {
        dlgs().openForkKbDialog({ kb: kb, onForked: function (nk) { if (nk && nk.number) window.location.assign("/kb/" + encodeURIComponent(nk.number) + "/"); } });
      });
      var dcBtn = host.querySelector("#kb-direct-contrib-btn");
      if (dcBtn && dlgs()) dcBtn.addEventListener("click", function () {
        dlgs().openNoThemePicker({ documentId: "" });
      });
    });
  }

  function renderView(host, bs, path) {
    var clean = path || window.location.pathname;
    if (clean === "/themes" || clean === "/explore/themes") {
      renderThemesList(host, bs);
    } else if (/^\/(?:explore\/)?themes\/(\d+)\/?$/.test(clean)) {
      renderThemeDetail(host, bs, RegExp.$1);
    } else if (clean === "/kb") {
      renderKbList(host, bs);
    } else if (/^\/kb\/(\d+)\/?$/.test(clean)) {
      renderKbDetail(host, bs, RegExp.$1);
    }
  }

  function boot() {
    var homeShowcase = document.getElementById("public-home-showcase");
    if (homeShowcase) {
      renderHomeShowcase(homeShowcase);
    }
    var rootEl = document.getElementById("public-kb-root");
    if (!rootEl) return;
    var bs = parseBootstrap() || {};
    if (bs.view === "theme_detail" && bs.ref) renderThemeDetail(rootEl, bs, bs.ref);
    else if (bs.view === "themes_list") renderThemesList(rootEl, bs);
    else if (bs.view === "kb_detail" && bs.ref) renderKbDetail(rootEl, bs, bs.ref);
    else if (bs.view === "kb_list") renderKbList(rootEl, bs);
    else renderView(rootEl, bs, window.location.pathname);

    rootEl.addEventListener("click", function (ev) {
      if (ev.metaKey || ev.ctrlKey || ev.shiftKey || ev.altKey) return;
      var a = ev.target && ev.target.closest ? ev.target.closest("a[href]") : null;
      if (!a) return;
      var href = a.getAttribute("href") || "";
      if (/^\/(?:themes|explore\/themes|kb)(?:\/|$)/.test(href)) {
        ev.preventDefault();
        if (window.history && window.history.pushState) {
          window.history.pushState(null, "", href);
        }
        renderView(rootEl, bs, href);
      }
    });

    window.addEventListener("popstate", function () {
      renderView(rootEl, bs, window.location.pathname);
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})(window);
