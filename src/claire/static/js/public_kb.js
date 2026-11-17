/* Public views of the shared knowledge bases and the themes: /kb, /kb/{number}/, /themes, /themes/{number}/ (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Sections 7 and 8.4). The data comes from the JSON form of the routes of docs/implementation/API_HTTP_CONTRACT.md. */
(function (root) {
  "use strict";

  function ui() { return root.ClaireUi; }
  function dlgs() { return root.ClaireKbDialogs; }
  function t(key, params) { return ui().t(key, params); }
  function esc(v) { return ui().esc(v); }
  function fail(res) { ui().toast(ui().errorText(res), "danger"); }

  function parseBootstrap() {
    var el = document.getElementById("claire-public-bootstrap");
    if (!el) return null;
    try { return JSON.parse(el.textContent || "{}"); } catch (_) { return null; }
  }

  function themeHref(number) { return "/themes/" + encodeURIComponent(number) + "/"; }
  function kbHref(number) { return "/kb/" + encodeURIComponent(number) + "/"; }
  function typeLabel(knowledgeType) { return t(knowledgeType === "fictional" ? "kb.dlg.type_fictional" : "kb.dlg.type_real"); }
  function typeTag(knowledgeType) { return '<span class="claire-tag ' + (knowledgeType === "fictional" ? "fic" : "acc") + '">' + esc(typeLabel(knowledgeType)) + "</span>"; }
  function loginHref() { return "/login?next=" + encodeURIComponent(window.location.pathname + window.location.search); }

  function publicNavHtml(current) {
    var links = [
      { id: "home", href: "/", label: t("kb.pub.nav_home") },
      { id: "themes", href: "/themes", label: t("kb.pub.nav_themes") },
      { id: "kb", href: "/kb", label: t("kb.pub.nav_kbs") },
      { id: "accounts", href: "/explore/accounts", label: t("kb.pub.nav_accounts") }
    ];
    return '<nav class="claire-pubnav" aria-label="' + esc(t("kb.pub.nav_label")) + '">' + links.map(function (it) {
      return '<a href="' + esc(it.href) + '"' + (it.id === current ? ' aria-current="page"' : "") + ">" + esc(it.label) + "</a>";
    }).join("") + "</nav>";
  }

  function typeChips(name, value) {
    return ui().renderChips({
      name: name,
      label: t("kb.pub.filter_type_aria"),
      value: value,
      options: [
        { value: "all", label: t("kb.pub.filter_all") },
        { value: "real", label: t("kb.pub.filter_real") },
        { value: "fictional", label: t("kb.pub.filter_fictional") }
      ]
    });
  }

  function typeFromQuery() {
    var value = new URLSearchParams(window.location.search).get("type");
    return value === "real" || value === "fictional" ? value : "all";
  }

  function setTypeQuery(value) {
    if (!window.history || !window.history.replaceState) return;
    window.history.replaceState(null, "", window.location.pathname + (value === "all" ? "" : "?type=" + encodeURIComponent(value)));
  }

  // The root page of network mode has the themes from the server (design Section 8.4). This part adds the links to the collections.
  function renderHomeShowcase(host) {
    host.innerHTML = publicNavHtml("home");
  }

  // GET /themes: the root of the collection has each theme in "themes" (number, url, label, description, knowledge_type, state, knowledge_bases = the count).
  function renderThemesList(host, bs) {
    var typeFilter = typeFromQuery();
    var themes = [];
    var loadError = null;
    ui().request("/themes").then(function (r) {
      themes = ((r.ok && r.data && r.data.themes) || []).filter(function (th) { return th.state === "active"; });
      loadError = r.ok ? null : r;
      paint();
    });

    function paint() {
      var list = themes.filter(function (th) { return typeFilter === "all" || th.knowledge_type === typeFilter; });
      var h = publicNavHtml("themes");
      h += '<div class="claire-page-head"><div><h1>' + ui().icon("folder") + " " + esc(t("kb.pub.themes_title")) + '</h1><p class="claire-page-sub">' + esc(t("kb.pub.themes_subtitle")) + "</p></div>";
      h += '<div class="claire-actions">' + typeChips("pub-themes-type", typeFilter);
      if (bs.is_authenticated) h += '<button type="button" class="claire-btn pri" id="pub-th-create-kb">' + ui().icon("plus") + " " + esc(t("campaign.make_kb_link")) + "</button>";
      h += "</div></div>";
      if (loadError) h += '<div class="claire-note warn"><span>' + esc(ui().errorText(loadError)) + "</span></div>";
      h += '<div class="claire-grid-3">';
      if (!list.length) {
        h += '<div class="claire-card"><p class="claire-hint">' + esc(t("kb.ui.empty_list")) + "</p></div>";
      } else {
        list.forEach(function (th) {
          h += '<article class="claire-card"><div class="claire-card-head"><h2><a href="' + esc(themeHref(th.number)) + '">' + esc(th.label) + "</a></h2>" + typeTag(th.knowledge_type) + "</div>";
          if (th.description) h += '<p class="claire-hint">' + esc(th.description) + "</p>";
          h += '<div class="claire-tags"><span class="claire-tag">' + esc(t("kb.pub.kb_count", { count: th.knowledge_bases || 0 })) + "</span></div></article>";
        });
      }
      h += "</div>";
      host.innerHTML = h;
      ui().enhance(host);
      host.querySelectorAll('input[name="pub-themes-type"]').forEach(function (inp) {
        inp.addEventListener("change", function () { typeFilter = inp.value; setTypeQuery(typeFilter); paint(); });
      });
      var createBtn = host.querySelector("#pub-th-create-kb");
      if (createBtn) createBtn.addEventListener("click", function () {
        dlgs().openCreateKbDialog({ defaultType: typeFilter === "fictional" ? "fictional" : "real", onCreated: function (kb) { if (kb && kb.number) window.location.assign(kbHref(kb.number)); } });
      });
    }
  }

  // GET /themes/{number}/: the theme with its knowledge bases, the shared ones first, then the attached public personal ones.
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
      var sharedKbs = kbs.filter(function (k) { return !k.is_personal; });
      var personalKbs = kbs.filter(function (k) { return !!k.is_personal; });

      h += '<div class="claire-page-head"><div><h1>' + esc(th.label) + " " + typeTag(th.knowledge_type) + "</h1>" +
        (th.description ? '<p class="claire-page-sub">' + esc(th.description) + "</p>" : "") +
        '<p class="claire-page-sub">' + esc(t("themes.counts", { knowledge_bases: th.shared_knowledge_bases || 0, contributions: th.visible_contributions || 0 })) + "</p></div>";
      h += '<div class="claire-actions">';
      if (bs.is_authenticated) {
        h += '<button type="button" class="claire-btn pri" id="th-contrib-btn">' + ui().icon("merge") + " " + esc(t("campaign.contribute_action")) + "</button>";
        h += '<button type="button" class="claire-btn sec" id="th-make-kb-btn">' + ui().icon("plus") + " " + esc(t("kb.pub.theme_make_kb")) + "</button>";
      } else {
        h += '<a class="claire-btn pri" href="' + esc(loginHref()) + '">' + esc(t("kb.pub.login_to_act")) + "</a>";
      }
      h += "</div></div>";

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
        dlgs().openCreateKbDialog({ defaultType: th.knowledge_type, onCreated: function (kb) { if (kb && kb.number) window.location.assign(kbHref(kb.number)); } });
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
    var typeFilter = typeFromQuery();
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
        if (typeFilter !== "all" && k.knowledge_type !== typeFilter) return false;
        return !joinedOnly || !!k.role;
      });
      var h = publicNavHtml("kb");
      h += '<div class="claire-page-head"><div><h1>' + ui().icon("folder") + " " + esc(t("kb.pub.kbs_title")) + '</h1><p class="claire-page-sub">' + esc(t("kb.pub.kbs_subtitle")) + "</p></div>";
      h += '<div class="claire-actions">' + typeChips("pub-kb-type", typeFilter);
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
            return '<a href="' + esc(kbHref(k.number)) + '"><strong>' + esc(k.label) + "</strong></a> " + typeTag(k.knowledge_type) +
              (k.state === "archived" ? ' <span class="claire-tag warn">' + esc(dlgs().stateLabel("archived")) + "</span>" : "") +
              (k.reembed_required ? ' <span class="claire-tag danger">' + esc(t("kb.pub.reembed_badge")) + "</span>" : "") +
              (k.description ? '<br><span class="claire-hint">' + esc(k.description) + "</span>" : "");
          } },
          { label: t("kb.pub.col_policy"), render: function (k) { return esc(t("kb.join_policy." + k.join_policy)); } },
          { label: t("kb.pub.col_role"), render: function (k) { return k.role ? '<span class="claire-tag ok">' + esc(t("kb.role." + k.role)) + "</span>" : "—"; } },
          { label: t("kb.pub.col_stats"), render: function (k) { return esc((k.stats && k.stats.documents) || 0) + " / " + esc((k.stats && k.stats.entities) || 0); } }
        ],
        rows: visible
      });
      h += "</section>";

      host.innerHTML = h;
      ui().enhance(host);
      host.querySelectorAll('input[name="pub-kb-type"]').forEach(function (inp) {
        inp.addEventListener("change", function () { typeFilter = inp.value; setTypeQuery(typeFilter); paint(); });
      });
      var joinedBox = host.querySelector("#pub-kb-joined");
      if (joinedBox) joinedBox.addEventListener("change", function () { joinedOnly = joinedBox.checked; paint(); });
      var makeBtn = host.querySelector("#pub-kb-make");
      if (makeBtn) makeBtn.addEventListener("click", function () {
        dlgs().openCreateKbDialog({ defaultType: typeFilter === "fictional" ? "fictional" : "real", onCreated: function () { load(); } });
      });
    }

    load();
  }

  // The page of one shared knowledge base. The data: its item of GET /kb, GET /kb/{number}/members, GET /kb/{number}/contributions, and GET /kb/{number}/documents for the titles.
  function renderKbDetail(host, bs, kbNum) {
    var pendingJoin = false;

    function load() {
      Promise.all([
        ui().request("/kb"),
        ui().request(kbHref(kbNum) + "contributions"),
        ui().request(kbHref(kbNum) + "members"),
        ui().request(kbHref(kbNum) + "documents")
      ]).then(function (all) {
        var kb = ((all[0].ok && all[0].data && all[0].data.knowledge_bases) || []).find(function (k) { return String(k.number) === String(kbNum); });
        var h = publicNavHtml("kb");
        h += '<div class="claire-crumb"><a href="/kb">' + esc(t("kb.pub.nav_kbs")) + "</a><span>/</span><span>" + esc(kbNum) + "</span></div>";
        if (!kb) {
          host.innerHTML = h + '<div class="claire-note warn"><span>' + esc(all[0].ok ? t("kb.err.kb_not_found") : ui().errorText(all[0])) + "</span></div>";
          return;
        }
        var contributions = (all[1].ok && all[1].data && all[1].data.contributions) || [];
        var members = (all[2].ok && all[2].data && all[2].data.members) || [];
        var titles = {};
        ((all[3].ok && all[3].data && all[3].data.documents) || []).forEach(function (d) { titles[String(d.id)] = d.title || ""; });
        var role = kb.role || "";
        var archived = kb.state === "archived";
        var canReview = bs.is_admin || role === "moderator" || role === "collaborator";

        h += '<section class="claire-card"><div class="claire-page-head"><div><h1>' + esc(kb.label) + " " + typeTag(kb.knowledge_type) +
          ' <span class="claire-tag">' + esc(t("kb.join_policy." + kb.join_policy)) + "</span>" +
          (role ? ' <span class="claire-tag ok">' + esc(t("kb.role." + role)) + "</span>" : "") +
          (archived ? ' <span class="claire-tag warn">' + esc(dlgs().stateLabel("archived")) + "</span>" : "") +
          "</h1>" + (kb.description ? '<p class="claire-page-sub">' + esc(kb.description) + "</p>" : "") +
          '<p class="claire-page-sub">' + esc(t("kb.dlg.col_docs")) + ": " + esc((kb.stats && kb.stats.documents) || 0) + " · " + esc(t("kb.dlg.col_entities")) + ": " + esc((kb.stats && kb.stats.entities) || 0) + "</p></div>";
        h += '<div class="claire-actions">';
        if (bs.is_authenticated) {
          if (role) {
            if (!archived) h += '<button type="button" class="claire-btn pri" id="kb-direct-contrib-btn">' + ui().icon("merge") + " " + esc(t("kb.pub.kb_direct_contribute")) + "</button>";
            h += '<button type="button" class="claire-btn sec" id="kb-leave-btn">' + esc(t("kb.pub.kb_leave")) + "</button>";
          } else if (pendingJoin) {
            h += '<button type="button" class="claire-btn sec" id="kb-leave-btn">' + esc(t("kb.pub.kb_withdraw_join")) + "</button>";
          } else if (!archived) {
            h += '<button type="button" class="claire-btn pri" id="kb-join-btn">' + esc(t(kb.join_policy === "open" ? "kb.pub.kb_join" : "kb.pub.kb_join_request")) + "</button>";
          }
          h += '<button type="button" class="claire-btn" id="kb-fork-btn">' + ui().icon("fork") + " " + esc(t("kb.dlg.fork_confirm")) + "</button>";
          if (canReview) h += '<a class="claire-btn sec" href="/settings/kb/' + esc(kbNum) + '">' + esc(t("kb.pub.kb_review_manage")) + "</a>";
        } else {
          h += '<a class="claire-btn pri" href="' + esc(loginHref()) + '">' + esc(t("kb.pub.login_to_act")) + "</a>";
        }
        h += "</div></div>";
        if (pendingJoin) h += '<div class="claire-note ok"><span>' + esc(t("kb.pub.kb_join_pending")) + "</span></div>";
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
              var title = esc(titles[String(c.document)] || c.document);
              return (c.state === "visible" ? '<a href="' + esc(kbHref(kbNum) + "?doc=" + encodeURIComponent(c.document)) + '"><strong>' + title + "</strong></a>" : "<strong>" + title + "</strong>") +
                (c.state !== "visible" ? ' <span class="claire-tag warn">' + esc(dlgs().stateLabel(c.state)) + "</span>" : "");
            } },
            { label: t("kb.dlg.col_contributor"), render: function (c) { return esc((c.contributor && (c.contributor.display_name || c.contributor.account)) || t("kb.pub.contributor_gone")); } },
            { label: t("kb.pub.col_origin"), render: function (c) { return esc(t("kb.origin." + c.origin)); } }
          ],
          rows: contributions
        });
        h += "</section></div></div>";

        host.innerHTML = h;
        ui().enhance(host);

        var joinBtn = host.querySelector("#kb-join-btn");
        if (joinBtn) joinBtn.addEventListener("click", function () {
          // POST /kb/{number}/membership: the join policy "open" gives an active membership, "approval" a pending one.
          ui().request(kbHref(kbNum) + "membership", { method: "POST", body: {} }).then(function (r) {
            if (!r.ok) { fail(r); return; }
            pendingJoin = !!(r.data && r.data.membership && r.data.membership.state === "pending");
            load();
          });
        });
        var leaveBtn = host.querySelector("#kb-leave-btn");
        if (leaveBtn) leaveBtn.addEventListener("click", function () {
          ui().request(kbHref(kbNum) + "membership", { method: "DELETE" }).then(function (r) {
            if (!r.ok) { fail(r); return; }
            pendingJoin = false;
            load();
          });
        });
        var forkBtn = host.querySelector("#kb-fork-btn");
        if (forkBtn) forkBtn.addEventListener("click", function () {
          dlgs().openForkKbDialog({ kb: kb, onForked: function (fork) { if (fork && fork.number) window.location.assign(kbHref(fork.number)); } });
        });
        var directBtn = host.querySelector("#kb-direct-contrib-btn");
        if (directBtn) directBtn.addEventListener("click", function () {
          dlgs().openNoThemePicker({ kbNumber: kbNum, homeHref: bs.home_href, onSubmitted: function () { load(); } });
        });
      });
    }

    load();
  }

  function boot() {
    var homeShowcase = document.getElementById("public-home-showcase");
    if (homeShowcase) renderHomeShowcase(homeShowcase);
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
