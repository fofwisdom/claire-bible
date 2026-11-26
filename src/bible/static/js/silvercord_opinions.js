/* --- Claire Bible: the 의견 tab of the Silvercord page (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 D, phase E7b) --- */
// The tab asks for the opinions only when the reader opens it (rule D7). The text of a remote post comes as data: paragraphs of text segments and http/https links. This file makes text nodes and links from them and never puts the markup of a remote server into the page (rule D2).
(function () {
  "use strict";

  // Levels of the reply tree on the page; a deeper level gets a link to the post on its server (rule D1).
  var MAX_DEPTH = 4;
  // The page asks again at most this number of times while a fetch runs (rule B6).
  var MAX_POLLS = 10;
  var LINK_REL = "nofollow noopener noreferrer";
  var LANG_RE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})*$/;

  function make(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    return node;
  }

  function webUrl(value) {
    var text = String(value || "");
    return /^https?:\/\//i.test(text) ? text : null;
  }

  function link(href, cls, text) {
    var url = webUrl(href);
    if (url === null) return make("span", cls, text);
    var a = make("a", cls, text);
    a.href = url;
    a.rel = LINK_REL;
    return a;
  }

  function formatTime(seconds) {
    if (typeof seconds !== "number" || !isFinite(seconds)) return null;
    var date = new Date(seconds * 1000);
    var text;
    try {
      text = new Intl.DateTimeFormat(document.documentElement.lang || undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
    } catch (_) {
      text = date.toISOString();
    }
    return { iso: date.toISOString(), text: text };
  }

  // The paragraphs of one post: each segment is {text} or {text, href}; a segment "\n" is a line break.
  function bodyNode(opinion) {
    var body = make("div", "op-post-body");
    if (opinion.language && LANG_RE.test(String(opinion.language))) body.lang = String(opinion.language);
    (Array.isArray(opinion.text) ? opinion.text : []).forEach(function (paragraph) {
      var p = make("p");
      (Array.isArray(paragraph) ? paragraph : []).forEach(function (segment) {
        var text = String((segment && segment.text) || "");
        if (segment && segment.href) p.appendChild(link(segment.href, "", text));
        else if (text === "\n") p.appendChild(document.createElement("br"));
        else p.appendChild(document.createTextNode(text));
      });
      body.appendChild(p);
    });
    return body;
  }

  function create(opts) {
    var root = opts.root;
    var url = opts.url;
    var rtr = opts.rtr;
    var live = opts.live;
    var reduceMotion = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
    var state = { started: false, phase: "loading", data: null, known: null, fresh: {}, polls: 0, timer: null, running: false, items: {} };

    function announce(text) {
      if (!live) return;
      live.textContent = "";
      // A new text node after an empty one makes each screen reader read the message.
      window.setTimeout(function () { live.textContent = text; }, 50);
    }

    function postNode(opinion) {
      var article = make("article", "op-post");
      article.dataset.iri = String(opinion.iri || "");
      if (state.fresh[opinion.iri]) article.classList.add("is-new");
      var meta = make("header", "op-post-meta");
      var author = opinion.author || {};
      meta.appendChild(link(author.iri, "op-post-author", author.name || author.handle || author.iri || ""));
      if (author.handle) meta.appendChild(make("span", "op-post-handle", author.handle));
      if (opinion.local) meta.appendChild(make("span", "chip-pill", rtr("share.sc.op_local_badge")));
      if (state.fresh[opinion.iri]) meta.appendChild(make("span", "op-new-mark", rtr("share.sc.op_new_mark")));
      var time = formatTime(opinion.published);
      if (time !== null) {
        var timeLink = link(opinion.url || opinion.iri, "op-post-time", "");
        var timeEl = make("time", "", time.text);
        timeEl.dateTime = time.iso;
        timeLink.appendChild(timeEl);
        meta.appendChild(timeLink);
      }
      if (opinion.hidden) {
        article.classList.add("is-hidden");
        meta.appendChild(make("span", "chip-pill", rtr("share.sc.op_hidden_badge")));
      }
      article.appendChild(meta);
      var body = bodyNode(opinion);
      if (opinion.summary || opinion.sensitive) {
        // A content warning stays closed until the reader opens it (rule D3).
        var details = make("details", "op-cw");
        details.appendChild(make("summary", "", opinion.summary || rtr("share.sc.op_cw_default")));
        details.appendChild(body);
        article.appendChild(details);
      } else {
        article.appendChild(body);
      }
      var actions = make("div", "op-post-actions");
      // RD30: an opinion of an account of this server has the same controls as an opinion of another server.
      if (!opinion.hidden && state.data && state.data.actions) clientButtons(opinion).forEach(function (btn) { actions.appendChild(btn); });
      if (opinion.frame_url && String(opinion.frame_url).charAt(0) === "/") actions.appendChild(frameButton(opinion));
      if (state.data && state.data.moderation && opinion.hideable) actions.appendChild(moderationButton(opinion));
      if (actions.childElementCount > 0) article.appendChild(actions);
      return article;
    }

    function postJson(url, method, body) {
      return fetch(url, { method: method, credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: body === null ? undefined : JSON.stringify(body) })
        .then(function (resp) {
          return resp.json().catch(function () { return {}; }).then(function (answer) {
            if (!resp.ok) throw new Error(String((answer && answer.error) || resp.status));
            return answer;
          });
        });
    }

    // The opinion as an item of the timeline of the account (phase E7d): the functions of the federation client work on the item.
    function ensureItem(opinion) {
      var known = state.items[opinion.iri];
      if (known && known.item_id) return Promise.resolve(known);
      return postJson(state.data.actions.item_url, "POST", { object: state.data.object, opinion: opinion.iri }).then(function (answer) {
        state.items[opinion.iri] = answer;
        return answer;
      });
    }

    function failed(error) {
      announce(rtr("share.sc.op_action_failed", { error: String((error && error.message) || error) }));
    }

    // Reply, like, boost, and quote of one opinion (rule D4). Like and boost are toggle buttons with aria-pressed.
    function clientButtons(opinion) {
      var itemState = state.items[opinion.iri] || {};
      var reply = make("button", "sc-btn op-act-reply", rtr("share.sc.op_reply"));
      reply.type = "button";
      reply.addEventListener("click", function () { openCompose({ kind: "reply", opinion: opinion }); });
      var quote = make("button", "sc-btn op-act-quote", rtr("share.sc.op_quote"));
      quote.type = "button";
      quote.addEventListener("click", function () { openCompose({ kind: "quote", opinion: opinion }); });
      var toggles = [["favourited", "favourite", "share.sc.op_like", "op-act-like"], ["boosted", "boost", "share.sc.op_boost", "op-act-boost"]].map(function (spec) {
        var btn = make("button", "sc-btn " + spec[3], rtr(spec[2]));
        btn.type = "button";
        btn.setAttribute("aria-pressed", itemState[spec[0]] ? "true" : "false");
        btn.addEventListener("click", function () {
          btn.disabled = true;
          ensureItem(opinion)
            .then(function (item) { return postJson("/api/federation/timeline/" + encodeURIComponent(item.item_id) + "/" + spec[1], item[spec[0]] ? "DELETE" : "POST", null); })
            .then(function (answer) {
              var item = state.items[opinion.iri];
              item.favourited = Boolean(answer.favourited);
              item.boosted = Boolean(answer.boosted);
              btn.setAttribute("aria-pressed", item[spec[0]] ? "true" : "false");
            })
            .catch(failed)
            .then(function () { btn.disabled = false; });
        });
        return btn;
      });
      return [reply, toggles[0], toggles[1], quote];
    }

    // The like of the post of the page (Section 5.6 rule 4): the favourite of the item of the object, as for each post (RD30). The count changes at once; the origin counts the delivered Like.
    function likePostButton(data) {
      var post = { iri: data.object };
      var btn = make("button", "sc-btn op-like-post", rtr("share.sc.op_like_post"));
      btn.type = "button";
      btn.setAttribute("aria-pressed", data.actions.liked ? "true" : "false");
      btn.addEventListener("click", function () {
        btn.disabled = true;
        ensureItem(post)
          .then(function (item) { return postJson("/api/federation/timeline/" + encodeURIComponent(item.item_id) + "/favourite", data.actions.liked ? "DELETE" : "POST", null); })
          .then(function (answer) {
            var liked = Boolean(answer.favourited);
            if (data.counts && liked !== data.actions.liked) data.counts.likes = Math.max(0, Number(data.counts.likes || 0) + (liked ? 1 : -1));
            data.actions.liked = liked;
            render();
          })
          .catch(function (error) {
            btn.disabled = false;
            failed(error);
          });
      });
      return btn;
    }

    // One compose dialog for a reply to an opinion, a quote of an opinion, and a reply to the object of the page (rules D4 and D9). The notes route of the federation client makes the note.
    var composeDialog = null;
    var composeTarget = null;
    function openCompose(target) {
      composeTarget = target;
      if (composeDialog === null) {
        composeDialog = make("dialog", "op-compose-dialog");
        composeDialog.id = "op-compose-dialog";
        composeDialog.setAttribute("closedby", "any");
        var form = make("form", "op-compose-form");
        var title = make("h2", "op-frame-title");
        title.id = "op-compose-title";
        composeDialog.setAttribute("aria-labelledby", title.id);
        var textLabel = make("label", "op-compose-field");
        textLabel.appendChild(make("span", "", rtr("share.sc.op_compose_text")));
        var text = make("textarea", "op-compose-text");
        text.name = "content";
        text.required = true;
        text.rows = 5;
        textLabel.appendChild(text);
        var cwLabel = make("label", "op-compose-field");
        cwLabel.appendChild(make("span", "", rtr("share.sc.op_compose_cw")));
        var cw = make("input", "op-compose-cw");
        cw.type = "text";
        cw.name = "summary";
        cw.maxLength = 500;
        cwLabel.appendChild(cw);
        var error = make("p", "op-alert op-compose-error");
        error.setAttribute("role", "alert");
        error.hidden = true;
        var row = make("div", "op-compose-buttons");
        var cancel = make("button", "sc-btn", rtr("share.sc.op_cancel"));
        cancel.type = "button";
        cancel.addEventListener("click", function () { composeDialog.close(); });
        var send = make("button", "sc-btn op-compose-send", rtr("share.sc.op_send"));
        send.type = "submit";
        row.appendChild(cancel);
        row.appendChild(send);
        [title, textLabel, cwLabel, error, row].forEach(function (node) { form.appendChild(node); });
        form.addEventListener("submit", function (event) {
          event.preventDefault();
          submitCompose(text, cw, error, send);
        });
        composeDialog.appendChild(form);
        document.body.appendChild(composeDialog);
      }
      var author = (target.opinion && target.opinion.author) || {};
      var name = author.name || author.handle || "";
      composeDialog.querySelector("#op-compose-title").textContent = target.kind === "object" ? rtr("share.sc.op_reply_object") : rtr(target.kind === "quote" ? "share.sc.op_compose_quote_title" : "share.sc.op_compose_reply_title", { name: name });
      var field = composeDialog.querySelector(".op-compose-text");
      field.value = target.kind === "reply" && author.handle ? author.handle + " " : "";
      composeDialog.querySelector(".op-compose-cw").value = "";
      composeDialog.querySelector(".op-compose-error").hidden = true;
      composeDialog.showModal();
      field.focus();
    }

    function submitCompose(text, cw, error, send) {
      var target = composeTarget;
      if (!target || !text.value.trim()) return;
      send.disabled = true;
      error.hidden = true;
      var body = { content: text.value };
      if (cw.value.trim()) {
        body.summary = cw.value.trim();
        body.sensitive = true;
      }
      var ready = target.kind === "object" ? Promise.resolve(null) : ensureItem(target.opinion);
      ready
        .then(function (item) {
          if (target.kind === "object") body.in_reply_to_object = state.data.object;
          else if (target.kind === "quote") body.quote_of = item.item_id;
          else body.in_reply_to = item.item_id;
          return postJson(state.data.actions.notes_url, "POST", body);
        })
        .then(function () {
          composeDialog.close();
          announce(rtr(target.kind === "quote" ? "share.sc.op_sent_quote" : "share.sc.op_sent_reply"));
          // The reply of this server shows in the tree at once (the route lists the notes of this server).
          state.polls = 0;
          load();
        })
        .catch(function (err) {
          error.textContent = rtr("share.sc.op_action_failed", { error: String((err && err.message) || err) });
          error.hidden = false;
        })
        .then(function () { send.disabled = false; });
    }

    // "서식과 첨부 보기" (rule D2): the formatted body and the attachments of one opinion in a sandbox frame of this origin (no script, an opaque origin), in one modal dialog of the page.
    var frameDialog = null;
    function frameButton(opinion) {
      var btn = make("button", "sc-btn op-frame-open", rtr("share.sc.op_formatted"));
      btn.type = "button";
      btn.addEventListener("click", function () { openFrame(opinion); });
      return btn;
    }
    function openFrame(opinion) {
      if (frameDialog === null) {
        frameDialog = make("dialog", "op-frame-dialog");
        frameDialog.id = "op-frame-dialog";
        frameDialog.setAttribute("closedby", "any");
        var head = make("div", "op-frame-head");
        var title = make("h2", "op-frame-title");
        title.id = "op-frame-title";
        frameDialog.setAttribute("aria-labelledby", title.id);
        var close = make("button", "sc-btn", rtr("share.sc.op_close"));
        close.type = "button";
        close.addEventListener("click", function () { frameDialog.close(); });
        head.appendChild(title);
        head.appendChild(close);
        var frame = make("iframe", "op-frame");
        frame.setAttribute("sandbox", "allow-popups allow-popups-to-escape-sandbox");
        frame.setAttribute("referrerpolicy", "no-referrer");
        frameDialog.appendChild(head);
        frameDialog.appendChild(frame);
        frameDialog.addEventListener("close", function () { frame.removeAttribute("src"); });
        document.body.appendChild(frameDialog);
      }
      var author = opinion.author || {};
      var label = rtr("share.sc.op_frame_title", { name: author.name || author.handle || "" });
      frameDialog.querySelector(".op-frame-title").textContent = label;
      var iframe = frameDialog.querySelector("iframe");
      iframe.title = label;
      iframe.src = String(opinion.frame_url);
      frameDialog.showModal();
    }

    // The author of the object and a server admin hide an opinion from the page, or show it again (rule D5).
    function moderationButton(opinion) {
      var btn = make("button", "sc-btn op-hide", rtr(opinion.hidden ? "share.sc.op_show" : "share.sc.op_hide"));
      btn.type = "button";
      btn.addEventListener("click", function () {
        var hidden = !opinion.hidden;
        btn.disabled = true;
        fetch(state.data.moderation.url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", Accept: "application/json" }, body: JSON.stringify({ object: state.data.object, opinion: opinion.iri, hidden: hidden }) })
          .then(function (resp) { return resp.ok ? resp.json() : Promise.reject(new Error(String(resp.status))); })
          .then(function (answer) {
            opinion.hidden = Boolean(answer.hidden);
            announce(rtr(opinion.hidden ? "share.sc.op_hidden_done" : "share.sc.op_shown_done"));
            render();
          })
          .catch(function () {
            btn.disabled = false;
            announce(rtr("share.sc.op_hide_failed"));
          });
      });
      return btn;
    }

    function countUnder(children, iri) {
      var list = children[iri] || [];
      return list.reduce(function (sum, child) { return sum + 1 + countUnder(children, child.iri); }, 0);
    }

    function treeNode(list, children, depth) {
      var ol = make("ol", depth === 1 ? "op-tree" : "op-tree op-children");
      list.forEach(function (opinion) {
        var li = make("li", "op-item");
        li.appendChild(postNode(opinion));
        var under = children[opinion.iri] || [];
        if (under.length > 0 && depth < MAX_DEPTH) {
          li.appendChild(treeNode(under, children, depth + 1));
        } else if (under.length > 0) {
          li.appendChild(link(opinion.url || opinion.iri, "op-continue", rtr("share.sc.op_continue", { count: countUnder(children, opinion.iri) })));
        }
        ol.appendChild(li);
      });
      return ol;
    }

    function countsNode(counts) {
      var ul = make("ul", "op-counts");
      ul.setAttribute("aria-label", rtr("share.sc.op_counts_aria"));
      [["replies", "share.sc.op_count_replies"], ["quotes", "share.sc.op_count_quotes"], ["likes", "share.sc.op_count_likes"], ["shares", "share.sc.op_count_shares"]].forEach(function (pair) {
        // A rendition of another server has no likes and boosts here (null): its origin counts them.
        if (counts && counts[pair[0]] === null) return;
        ul.appendChild(make("li", "", rtr(pair[1], { count: Number((counts && counts[pair[0]]) || 0) })));
      });
      return ul;
    }

    function retryButton() {
      var btn = make("button", "sc-btn op-retry", rtr("share.sc.op_retry"));
      btn.type = "button";
      btn.addEventListener("click", function () {
        state.polls = 0;
        state.phase = "loading";
        update();
        load();
      });
      return btn;
    }

    // The state of a request or of a fetch (rule D8): a waiting state is a <progress> with a text, not a live region; a failure is an alert with the opinions that the server has and "다시 시도".
    function statusNode() {
      var box = make("div", "op-status");
      if (state.phase === "loading" || state.phase === "running") {
        var progress = make("progress", "op-progress");
        var labelId = "op-progress-label";
        progress.setAttribute("aria-labelledby", labelId);
        box.appendChild(progress);
        var label = make("span", "", rtr(state.phase === "loading" ? "share.sc.op_loading" : "share.sc.op_fetching"));
        label.id = labelId;
        box.appendChild(label);
        return box;
      }
      if (state.phase === "failed" || state.phase === "load_failed") {
        var alert = make("p", "op-alert", rtr(state.phase === "failed" ? "share.sc.op_fetch_failed" : "share.sc.op_load_failed"));
        alert.setAttribute("role", "alert");
        box.appendChild(alert);
        box.appendChild(retryButton());
        return box;
      }
      if (state.phase === "stalled") {
        box.appendChild(make("p", "op-note", rtr("share.sc.op_still_running")));
        box.appendChild(retryButton());
        return box;
      }
      return null;
    }

    function hintNode() {
      var box = make("div", "op-reply-hint");
      // A signed-in actor replies from this server (rule D9, second sentence).
      if (state.data && state.data.actions) {
        var replyObject = make("button", "sc-btn op-reply-object", rtr("share.sc.op_reply_object"));
        replyObject.type = "button";
        replyObject.addEventListener("click", function () { openCompose({ kind: "object", opinion: null }); });
        box.appendChild(replyObject);
        return box;
      }
      box.appendChild(make("p", "", rtr("share.sc.op_reply_hint")));
      var row = make("div", "op-address-row");
      var address = window.location.origin + window.location.pathname;
      row.appendChild(make("code", "op-address", address));
      if (navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
        var copy = make("button", "sc-btn", rtr("share.sc.op_copy_address"));
        copy.type = "button";
        copy.addEventListener("click", function () {
          navigator.clipboard.writeText(address).then(function () { announce(rtr("share.sc.op_copied")); }, function () {});
        });
        row.appendChild(copy);
      }
      box.appendChild(row);
      return box;
    }

    function render() {
      var data = state.data;
      var box = make("div", "op-root");
      // The posts of other people are not indexed under this server (rule D7).
      box.setAttribute("data-nosnippet", "");
      if (data && data.published === false) {
        var notice = make("div", "op-private-box");
        notice.id = "op-not-published";
        notice.appendChild(make("strong", "", rtr("share.sc.op_not_published_title")));
        notice.appendChild(make("span", "", rtr("share.sc.op_not_published_desc")));
        box.appendChild(notice);
        root.replaceChildren(box);
        return;
      }
      if (data) {
        var top = make("div", "op-top");
        top.appendChild(countsNode(data.counts));
        if (data.actions && data.actions.post) top.appendChild(likePostButton(data));
        box.appendChild(top);
      }
      var status = statusNode();
      if (status) box.appendChild(status);
      if (data) {
        var objectIri = String(data.object || "");
        var opinions = Array.isArray(data.opinions) ? data.opinions : [];
        var byIri = {};
        opinions.forEach(function (o) { byIri[o.iri] = o; });
        var children = {};
        var roots = [];
        var quotes = [];
        opinions.forEach(function (o) {
          if (o.kind === "quote") {
            quotes.push(o);
            return;
          }
          var parent = byIri[o.parent];
          if (o.parent !== objectIri && parent && parent.kind === "reply") (children[o.parent] = children[o.parent] || []).push(o);
          else roots.push(o);
        });
        if (roots.length > 0) {
          var thread = make("section", "op-thread");
          thread.setAttribute("aria-label", rtr("share.sc.op_replies_title"));
          thread.appendChild(treeNode(roots, children, 1));
          box.appendChild(thread);
        }
        if (quotes.length > 0) {
          var group = make("section", "op-quotes");
          var heading = make("h3", "op-group-title", rtr("share.sc.op_quotes_title"));
          heading.id = "op-quotes-title";
          group.setAttribute("aria-labelledby", heading.id);
          group.appendChild(heading);
          group.appendChild(treeNode(quotes, {}, 1));
          box.appendChild(group);
        }
        if (opinions.length === 0 && state.phase !== "loading" && state.phase !== "running") box.appendChild(make("p", "op-empty", rtr("share.sc.op_empty")));
        box.appendChild(hintNode());
      }
      root.replaceChildren(box);
      deferThreads(box);
    }

    // Long opinion trees (Section 5.10 rule 6): each top-level thread that starts below the first screen gets content-visibility: auto (silvercord.css); never a thread on the first screen.
    function deferThreads(box) {
      var firstScreen = window.innerHeight || 800;
      box.querySelectorAll(".op-thread > .op-tree > .op-item, .op-quotes > .op-tree > .op-item").forEach(function (item) {
        if (item.getBoundingClientRect().top > firstScreen) item.classList.add("is-deferred");
      });
    }

    // The new opinions come with the element-scoped view transition of the body area (Section 5.10 rule 4) where the browser has it and the reader permits motion.
    function update(animate) {
      if (animate && root.childElementCount > 0 && "startViewTransition" in Element.prototype && !(reduceMotion && reduceMotion.matches)) {
        var transition = root.startViewTransition(render);
        transition.ready.catch(function () {});
      } else {
        render();
      }
    }

    function onData(data) {
      var iris = (Array.isArray(data.opinions) ? data.opinions : []).map(function (o) { return o.iri; });
      if (state.known === null) {
        state.known = {};
        iris.forEach(function (iri) { state.known[iri] = true; });
      }
      var fresh = {};
      var freshCount = 0;
      iris.forEach(function (iri) {
        if (!state.known[iri]) {
          fresh[iri] = true;
          freshCount += 1;
        }
      });
      state.fresh = fresh;
      var states = data.actions && data.actions.states ? data.actions.states : {};
      Object.keys(states).forEach(function (iri) {
        var known = state.items[iri] || {};
        known.favourited = Boolean(states[iri].favourited);
        known.boosted = Boolean(states[iri].boosted);
        state.items[iri] = known;
      });
      // The first answer replaces the loading text at once; a later answer with new opinions comes with the transition.
      var animate = state.data !== null && freshCount > 0;
      state.data = data;
      var refresh = data.refresh;
      if (data.published !== false && refresh && refresh.status === "running") {
        if (state.polls < MAX_POLLS) {
          state.phase = "running";
          state.polls += 1;
          state.timer = window.setTimeout(load, Math.max(1, Number(refresh.retry) || 3) * 1000);
        } else {
          state.phase = "stalled";
        }
      } else if (data.published !== false && refresh && refresh.status === "failed") {
        state.phase = "failed";
      } else {
        state.phase = "finished";
      }
      // One message when a fetch that the reader waited for ends (rule D8).
      if (state.running && state.phase === "finished") announce(freshCount > 0 ? rtr("share.sc.op_fetch_done", { count: freshCount }) : rtr("share.sc.op_fetch_done_none"));
      state.running = state.phase === "running";
      update(animate);
    }

    function load() {
      state.timer = null;
      fetch(url, { headers: { Accept: "application/json" }, credentials: "same-origin" })
        .then(function (resp) { return resp.ok ? resp.json() : Promise.reject(new Error(String(resp.status))); })
        .then(onData)
        .catch(function () {
          state.phase = "load_failed";
          state.running = false;
          update();
        });
    }

    return {
      // Called each time the tab opens; the first call starts the request.
      open: function () {
        if (state.started) return;
        state.started = true;
        render();
        load();
      },
      // The page shows another rendition (a rendition of another server, Section 5.11 C2, or the rendition of the page again): the tab gets the opinions of that object. A tab that was open asks at once; else it asks at its next open.
      setUrl: function (next) {
        if (!next || next === url) return;
        url = next;
        if (state.timer !== null) window.clearTimeout(state.timer);
        var wasStarted = state.started;
        state = { started: false, phase: "loading", data: null, known: null, fresh: {}, polls: 0, timer: null, running: false, items: {} };
        root.replaceChildren();
        if (wasStarted) this.open();
      }
    };
  }

  window.ClaireSilvercordOpinions = { create: create };
})();
