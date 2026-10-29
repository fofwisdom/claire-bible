/* Claire Bible — Syndication 연합 클라이언트 탭 (ActivityPub · AT Protocol 연결 서버 계정 경유 클라이언트) */
/*
 * 설계 원칙
 * 1. 서버 경유: Claire 는 Actor/Inbox/HTTP 서명을 직접 운영하지 않고, 테마에 연결된 기존 서버 계정의
 *    Client API(Mastodon REST · AT Protocol PDS XRPC)를 Claire 서버 프록시로 호출한다.
 * 2. 기능 기준: ActivityPub 클라이언트 기능은 Mastodon 5.0 이상이 제공하는 클라이언트 기능 전체를 갖춘다
 *    (CLAIRE_SYND_AP_FEATURES). Mastodon API 를 제공하는 다른 서버도 연결하며, 서버가 제공하지 않는 기능만 비활성으로 표시한다.
 * 3. 수집 ≠ 적재: 타임라인 게시물은 '수집함 보내기(s)'로 지식베이스 수집 원문이 되고, 엔티티는 적재(i) 시에만 생성된다.
 *    해시태그·facet 태그는 적재 전까지 #수집 태그일 뿐이며 [[Entity]] 로 표시하지 않는다.
 * 4. 결정론적 힌트: 테마 DB 기존 엔티티와 해시태그의 정규화 일치만 표시한다 (LLM 미사용).
 */
window.CLAIRE_SYND_CLIENT_FACTORY = function (api) {
  const { prefs, esc, ticker, protoDot } = api;
  const state = api.state;
  const FEATURES = window.CLAIRE_SYND_AP_FEATURES || [];
  const cs = {
    accountId: null, timelineId: null, prevTimelineId: null, selectedPostId: null,
    search: "", serverQuery: "", profile: null, exploreTab: "statuses", notifFilter: "all",
    showRequests: false, menuPostId: null, listPicker: false, noteEdit: false,
    revealed: new Set(), translated: new Set(), historyOpen: new Set(), altOpen: new Set()
  };
  const VIRTUAL = { sent: "📥 지식베이스로 보낸 게시물", features: "📋 클라이언트 기능 기준 (Mastodon 5.0+)" };
  const VIS = { public: ["🌐", "공개"], unlisted: ["🌙", "조용한 공개"], private: ["🔒", "팔로워 전용"], direct: ["✉️", "개인 메시지"] };
  const LANG = { ko: "한국어", en: "English", ja: "日本語" };
  const NOTIF = {
    mention: ["🗨", "님이 회원님을 언급했습니다"],
    reblog: ["🔁", "님이 회원님의 게시물을 부스트했습니다"],
    favourite: ["★", "님이 회원님의 게시물을 좋아합니다"],
    follow: ["👤", "님이 회원님을 팔로우했습니다"],
    quote: ["❝", "님이 회원님의 게시물을 인용했습니다"],
    poll: ["📊", "참여한 투표가 마감되었습니다"],
    update: ["✏️", "님이 부스트한 게시물을 수정했습니다"]
  };
  /* Mastodon 세분화 scope (canPublish 가 꺼진 읽기 전용 계정은 모든 쓰기 동작이 비활성) */
  const SCOPE_FOR = {
    post: "write:statuses", reply: "write:statuses", boost: "write:statuses", quote: "write:statuses", vote: "write:statuses",
    edit: "write:statuses", delete: "write:statuses", pin: "write:accounts", note: "write:accounts", favourite: "write:favourites",
    bookmark: "write:bookmarks", follow: "write:follows", list: "write:lists", mute: "write:mutes", muteConv: "write:mutes",
    block: "write:blocks", filter: "write:filters", report: "write:reports", notifications: "write:notifications", media: "write:media"
  };
  const panel = () => document.getElementById("panel-client");

  function theme() { return api.curTheme(); }
  function posts() { const t = theme(); if (!Array.isArray(t.posts)) t.posts = []; return t.posts; }
  function accounts() { return api.themeAccounts(); }
  function account(id) { return api.findAccount(id); }
  function apAccounts() { return accounts().filter((a) => a.proto === "ap"); }
  function byId(id) { return posts().find((p) => p.id === id) || null; }
  function postProto(post) { const a = account(post.accountId); return a ? a.proto : "ap"; }
  function supports(acc, feature) { return api.accountSupports(acc, feature); }
  function norm(s) { return String(s || "").toLowerCase().replace(/[\s\-_()#]+/g, ""); }
  function entityMatch(tag) { return theme().existingEntities.find((e) => norm(e.name) === norm(tag)) || null; }
  function domainOf(handle) { const m = String(handle || "").match(/^@[^@]+@(.+)$/); return m ? m[1] : ""; }
  function toggleKey(set, key) { if (set.has(key)) set.delete(key); else set.add(key); }
  function timelinesOf(acc) { return acc.timelines.filter((t) => !t.feature || supports(acc, t.feature)); }
  function isOwnHandle(handle) { return accounts().some((a) => a.handle === handle); }

  function canWrite(acc, kind) {
    if (!acc || !acc.canPublish) return false;
    if (acc.proto === "at") return true;
    const need = SCOPE_FOR[kind] || "write:statuses";
    return acc.scopes.includes("write") || acc.scopes.includes(need);
  }

  function guard(acc, kind, label) {
    if (canWrite(acc, kind)) return true;
    ticker(`🔒 ${acc ? acc.handle : "계정"} — ${label}: 쓰기 권한(${SCOPE_FOR[kind] || "write"})이 없는 읽기 전용 연결 계정입니다.`);
    return false;
  }

  function needFeature(acc, feature, label) {
    if (supports(acc, feature)) return true;
    ticker(`ⓘ 연결 서버(${acc ? acc.server : "-"})가 '${label}' 기능을 제공하지 않아 비활성입니다 (Mastodon 5.0 기준 기능).`);
    return false;
  }

  /* 연결 서버 API 호출 표기 (Claire 서버 프록시 경유) */
  function apCall(acc, method, path, body) { return { method, url: `https://${acc.server}${path}`, body }; }
  function atCall(acc, nsid, body) { return { method: "POST", url: `https://${acc.server}/xrpc/${nsid}`, body }; }
  function announce(label, req) {
    ticker(`${label} → ${req.method} ${req.url}${req.body && req.body.collection ? ` (${req.body.collection})` : ""}`);
  }

  function ensureSelection() {
    if (VIRTUAL[cs.timelineId]) return;
    const accs = accounts();
    if (!cs.accountId || !account(cs.accountId)) {
      cs.accountId = accs[0] ? accs[0].id : null;
      cs.timelineId = null;
    }
    const acc = account(cs.accountId);
    if (cs.timelineId === "search") return;
    if (acc && (!cs.timelineId || !timelinesOf(acc).some((t) => t.id === cs.timelineId))) {
      const first = timelinesOf(acc)[0];
      cs.timelineId = first ? first.id : null;
    }
  }

  function currentTimeline() {
    const acc = account(cs.accountId);
    return acc ? acc.timelines.find((t) => t.id === cs.timelineId) || null : null;
  }

  function switchTimeline(accId, tlId) {
    if (accId) cs.accountId = accId;
    cs.timelineId = tlId;
    cs.selectedPostId = null;
    cs.profile = null;
  }

  function ensureTagTimeline(acc, name) {
    const id = `tag:${name}`;
    if (!acc.timelines.some((t) => t.id === id)) {
      const idx = acc.timelines.findIndex((t) => t.id === "favourites");
      const tl = { id, icon: "#", label: `#${name}`, api: `GET /api/v1/timelines/tag/${name}`, stream: "hashtag", collectable: true, dynamic: true };
      acc.timelines.splice(idx >= 0 ? idx : acc.timelines.length, 0, tl);
    }
    return id;
  }

  /* ── 뮤트 · 차단 · 필터 ── */
  function hiddenReason(post, acc) {
    if (!acc || !post.handle || post.own) return null;
    if ((acc.blocks || []).some((b) => b.handle === post.handle)) return "blocked";
    if ((acc.mutes || []).some((m) => m.handle === post.handle)) return "muted";
    if ((acc.domainBlocks || []).includes(domainOf(post.handle))) return "domain";
    return null;
  }

  function filterContext(tlId) {
    if (cs.profile) return "account";
    if (!tlId || tlId === "home" || tlId.startsWith("list:")) return "home";
    if (tlId === "notifications") return "notifications";
    if (tlId === "conversations") return "thread";
    return "public";
  }

  function matchFilter(post, acc, context) {
    if (!acc || !post.text || post.own) return null;
    const hay = `${post.cw || ""} ${post.text}`.toLowerCase();
    return (acc.filters || []).find((f) => f.context.includes(context) && f.keywords.some((k) => hay.includes(k.toLowerCase()))) || null;
  }

  function trendPosts(acc) {
    return ((acc && acc.trends && acc.trends.statuses) || []).map(byId).filter(Boolean);
  }

  function visiblePosts() {
    if (cs.timelineId === "sent") return posts().filter((p) => p.kbItemId).sort((a, b) => b.timestamp - a.timestamp);
    if (cs.timelineId === "search") return searchResults().posts;
    const acc = account(cs.accountId);
    if (!acc) return [];
    const tl = cs.timelineId || "";
    const q = cs.search.trim().toLowerCase();
    const ctx = filterContext(tl);
    let list = posts().filter((p) => p.accountId === acc.id).filter((p) => {
      if (tl === "favourites") return p.favourited && !p.notifOnly;
      if (tl === "bookmarks") return p.bookmarked && !p.notifOnly;
      if (tl === "notifications" && cs.notifFilter === "mention") return (p.timelines || []).includes(tl) && p.notif && p.notif.kind === "mention";
      if ((p.timelines || []).includes(tl)) return true;
      return tl.startsWith("tag:") && !p.notifOnly && (p.hashtags || []).some((h) => norm(h) === norm(tl.slice(4)));
    }).filter((p) => {
      if (hiddenReason(p, acc)) return false;
      const f = matchFilter(p, acc, ctx);
      return !(f && f.action === "hide");
    });
    if (q) {
      list = list.filter((p) => `${p.author} ${p.handle} ${p.cw || ""} ${p.text} ${(p.hashtags || []).map((t) => `#${t}`).join(" ")} ${p.card ? `${p.card.title} ${p.card.description}` : ""}`.toLowerCase().includes(q));
    }
    return tl === "scheduled" ? list.sort((a, b) => a.timestamp - b.timestamp) : list.sort((a, b) => b.timestamp - a.timestamp);
  }

  function searchResults() {
    const q = cs.serverQuery.trim();
    const ql = q.toLowerCase().replace(/^[#@]/, "");
    const acc = account(cs.accountId);
    const handles = new Set(Object.keys(theme().profiles || {}));
    posts().forEach((p) => { if (p.handle && !p.notifOnly) handles.add(p.handle); });
    const accountsRes = [...handles].filter((h) => {
      const pr = (theme().profiles || {})[h];
      return h.toLowerCase().includes(ql) || (pr && `${pr.name} ${pr.bio}`.toLowerCase().includes(ql));
    });
    const asHandle = q.startsWith("@") ? q : `@${q}`;
    const remote = /^@?[\w.-]+@[\w.-]+\.[a-z]{2,}$/i.test(q) && !handles.has(asHandle) ? asHandle : null;
    const tagSet = new Set();
    posts().forEach((p) => (p.hashtags || []).forEach((h) => tagSet.add(h)));
    ((acc && acc.trends && acc.trends.tags) || []).forEach((t) => tagSet.add(t.name));
    const tags = [...tagSet].filter((t) => norm(t).includes(norm(ql)));
    const postRes = posts().filter((p) => !p.notifOnly && !p.scheduled && (!acc || p.accountId === acc.id))
      .filter((p) => `${p.text} ${(p.hashtags || []).join(" ")} ${p.author}`.toLowerCase().includes(ql))
      .sort((a, b) => b.timestamp - a.timestamp);
    return { accounts: accountsRes.slice(0, 6), remote, tags: tags.slice(0, 8), posts: postRes };
  }

  function title() {
    ensureSelection();
    if (VIRTUAL[cs.timelineId]) return VIRTUAL[cs.timelineId];
    if (cs.timelineId === "search") return `🔎 '${cs.serverQuery}' 검색`;
    const acc = account(cs.accountId);
    const tl = currentTimeline();
    if (!acc || !tl) return "연합 클라이언트 (연결 계정 없음)";
    return `${tl.icon} ${tl.label} — ${acc.handle}`;
  }

  /* ── 게시물 ↔ 지식베이스 수집 항목 관계 (직접 보낸 항목 또는 URL/객체 URI 일치 = canonical_url 중복 수집 방지) ── */
  function kbItemFor(post) {
    if (!post || post.notifOnly || post.scheduled) return null;
    const items = theme().items;
    if (post.kbItemId) {
      const it = items.find((i) => i.id === post.kbItemId);
      if (it) return { item: it, via: "sent" };
    }
    const urls = [post.url, post.card && post.card.url].filter(Boolean);
    const hit = items.find((i) => urls.includes(i.url) || (post.uri && (i.apObjectUri === post.uri || i.atUri === post.uri)));
    return hit ? { item: hit, via: "url" } : null;
  }

  function findPostForKbItem(item) {
    const real = posts().filter((p) => !p.notifOnly && !p.scheduled);
    return real.find((p) => p.kbItemId === item.id)
      || real.find((p) => p.uri && (p.uri === item.apObjectUri || p.uri === item.atUri))
      || real.find((p) => p.url === item.url || (p.card && p.card.url === item.url))
      || null;
  }

  function postTarget(post) {
    return {
      proto: postProto(post), remoteId: post.remoteId, uri: post.uri, cid: post.cid, url: post.url,
      title: post.card ? post.card.title : `${post.author} (${post.handle})`, description: post.text
    };
  }

  function affinityScore(post) {
    const matches = (post.hashtags || []).filter((t) => entityMatch(t)).length;
    return Math.min(99, 50 + matches * 15 + (post.card ? 10 : 0));
  }

  /* ── 렌더링 조각 ── */
  function textHtml(text) {
    return esc(text)
      .replace(/(^|\s)(#[\p{L}\p{N}_]+)/gu, '$1<span class="cl-hashtag">$2</span>')
      .replace(/(^|\s)(@[\w.-]+(?:@[\w.-]+)?)/g, '$1<span class="cl-mention">$2</span>')
      .replace(/\n/g, "<br/>");
  }

  function initial(name) { return esc((String(name || "?").replace(/^@/, "").trim()[0] || "?").toUpperCase()); }

  function byNames(n) {
    const by = Array.isArray(n.by) ? n.by : [n.by];
    const names = by.slice(0, 2).map((h) => `<button type="button" class="cl-name" data-cl-profile="${esc(h)}"><b>${esc(String(h).split("@")[1] || h)}</b></button>`).join(", ");
    const rest = (n.total || by.length) - Math.min(2, by.length);
    return `${names}${rest > 0 ? ` 외 ${rest}명` : ""}`;
  }

  function notifLine(post) {
    if (!post.notif) return "";
    const [icon, tail] = NOTIF[post.notif.kind] || ["🔔", ""];
    return `<div class="cl-notif">${icon} ${byNames(post.notif)}${tail}</div>`;
  }

  function boostLine(post) {
    if (!post.rebloggedBy) return "";
    return `<div class="cl-notif">🔁 <button type="button" class="cl-name" data-cl-profile="${esc(post.rebloggedBy.handle)}"><b>${esc(post.rebloggedBy.name)}</b></button> 님이 부스트함</div>`;
  }

  function kbBadge(post) {
    const rel = kbItemFor(post);
    if (!rel) return "";
    if (rel.item.ingestState === "linked" && rel.item.linkedDoc) {
      return `<span class="tt-link-dot linked" title="지식베이스 적재 완료 (${esc(rel.item.linkedDoc.docId)})${rel.via === "url" ? " · 동일 URL" : ""}">🔗 적재</span>`;
    }
    return `<span class="tt-link-dot cl-inbox" title="지식베이스 수집함에 있음 (미적재 · 엔티티는 적재 시 생성)">📥 수집함</span>`;
  }

  function postHead(post) {
    const acc = account(post.accountId);
    const v = VIS[post.visibility || "public"];
    return `<div class="cl-post-head">
      <button type="button" class="cl-avatar proto-${acc ? acc.proto : "ap"}" data-cl-profile="${esc(post.handle)}" title="프로필 보기" aria-label="${esc(post.author)} 프로필 보기">${initial(post.author)}</button>
      <div class="cl-post-who"><button type="button" class="cl-name" data-cl-profile="${esc(post.handle)}"><b>${esc(post.author)}</b></button> <span class="cl-handle">${esc(post.handle)}</span></div>
      <div class="tt-card-meta-right">
        ${post.pinned ? `<span class="cl-meta-chip" title="프로필에 고정됨">📌</span>` : ""}
        ${kbBadge(post)}
        ${post.lang && post.lang !== "ko" ? `<span class="cl-lang" title="${esc(LANG[post.lang] || post.lang)}">${esc(post.lang.toUpperCase())}</span>` : ""}
        ${acc && acc.proto === "ap" ? `<span class="cl-vis" title="${v[1]}">${v[0]}</span>` : ""}
        ${post.edited ? `<button type="button" class="cl-edited" data-cl-history="${esc(post.id)}" title="수정 이력 보기 (GET /api/v1/statuses/:id/history)">수정됨</button>` : ""}
        <span class="tt-card-time">${esc(post.time)}</span>
      </div>
    </div>`;
  }

  function matchedTagsLine(post) {
    const matched = (post.hashtags || []).filter((t) => entityMatch(t));
    if (!matched.length) return "";
    return `<div class="cl-match-line" title="테마 DB 기존 엔티티와 해시태그 이름이 일치합니다 (결정론적 비교 · 적재 전이므로 엔티티로 연결되지 않음)">🏷️ 테마 엔티티 이름 일치: ${matched.map((t) => `<span class="tt-entity-chip candidate">#${esc(t)} <small style="color:var(--accent)">[일치]</small></span>`).join("")}</div>`;
  }

  function linkCard(card, full) {
    if (!card) return "";
    return `<a class="cl-link-card" href="${esc(card.url)}" target="_blank" rel="noopener noreferrer">
      <span class="cl-link-host">${esc((card.url.match(/^https?:\/\/([^/]+)/) || [])[1] || card.url)}</span>
      <strong>${esc(card.title)}</strong>
      ${card.description ? `<span class="cl-link-desc ${full ? "is-full" : ""}">${esc(card.description)}</span>` : ""}
      ${card.author ? `<span class="cl-link-author">작성자 · ${esc(card.author)}</span>` : ""}
    </a>`;
  }

  function mediaHtml(post) {
    const hidden = post.sensitive && !cs.revealed.has(`${post.id}:media`);
    const tiles = post.media.map((m, i) => `<figure class="cl-media-tile">
        <div class="cl-media-img" aria-hidden="true">🖼<span>${esc(m.label || "image")}</span></div>
        <button type="button" class="cl-alt-badge" data-cl-alt="${esc(post.id)}:${i}" title="대체 텍스트 보기" ${hidden ? "tabindex=\"-1\"" : ""}>ALT</button>
      </figure>`).join("");
    const alts = post.media.map((m, i) => (cs.altOpen.has(`${post.id}:${i}`) ? `<div class="cl-alt-text"><b>대체 텍스트</b> ${esc(m.alt || "(작성자가 대체 텍스트를 넣지 않음)")}</div>` : "")).join("");
    return `<div class="cl-media count-${Math.min(post.media.length, 4)} ${hidden ? "is-hidden" : ""}">
      ${tiles}
      ${hidden
        ? `<button type="button" class="cl-media-cover" data-cl-reveal="${esc(post.id)}:media">민감한 미디어 · 보려면 클릭</button>`
        : (post.sensitive ? `<button type="button" class="cl-media-hide" data-cl-reveal="${esc(post.id)}:media" title="다시 가리기">숨기기</button>` : "")}
    </div>${alts}`;
  }

  function pollHtml(post) {
    const poll = post.poll;
    const acc = account(post.accountId);
    const total = poll.options.reduce((n, o) => n + o.votes, 0);
    if (!(poll.voted || poll.expired || post.own)) {
      return `<div class="cl-poll" data-cl-poll="${esc(post.id)}">
        ${poll.options.map((o, i) => `<label class="cl-poll-opt"><input type="${poll.multiple ? "checkbox" : "radio"}" name="poll-${esc(post.id)}" value="${i}"/> ${esc(o.title)}</label>`).join("")}
        <div class="cl-poll-foot"><button type="button" class="tt-btn" data-cl-vote="${esc(post.id)}" ${canWrite(acc, "vote") ? "" : "disabled"}>투표</button><span>${total}명 참여 · ${esc(poll.expires)}${poll.multiple ? " · 복수 선택" : ""}</span></div>
      </div>`;
    }
    return `<div class="cl-poll">
      ${poll.options.map((o, i) => {
        const pct = total ? Math.round((o.votes / total) * 100) : 0;
        const mine = (poll.ownVotes || []).includes(i);
        return `<div class="cl-poll-result ${mine ? "mine" : ""}"><span class="cl-poll-bar" style="width:${pct}%"></span><span class="cl-poll-label">${mine ? "✓ " : ""}${esc(o.title)}</span><b>${pct}%</b></div>`;
      }).join("")}
      <div class="cl-poll-foot"><span>${total}명 참여 · ${esc(poll.expired ? "마감됨" : poll.expires)}${post.own && !poll.voted ? " · 내 투표" : ""}</span></div>
    </div>`;
  }

  function quoteHtml(post, full) {
    if (post.quoteState === "revoked") return `<div class="cl-quote is-void">원 작성자가 인용을 철회했습니다.</div>`;
    if (post.quoteState === "pending") return `<div class="cl-quote is-void">원 작성자의 인용 승인을 기다리는 중입니다.</div>`;
    const q = byId(post.quoteOf);
    if (!q) return `<div class="cl-quote is-void">삭제되었거나 볼 수 없는 게시물입니다.</div>`;
    const hideCw = q.cw && !cs.revealed.has(`${q.id}:cw`);
    return `<div class="cl-quote" data-cl-open="${esc(q.id)}" role="link" tabindex="0" title="인용된 게시물 열기">
      <div class="cl-quote-head"><span class="cl-avatar is-small proto-${postProto(q)}" aria-hidden="true">${initial(q.author)}</span><b>${esc(q.author)}</b> <span class="cl-handle">${esc(q.handle)}</span> <span class="tt-card-time">${esc(q.time)}</span></div>
      <div class="cl-post-text ${full ? "" : "clamp-3"}">${hideCw ? `⚠ ${esc(q.cw)}` : textHtml(q.text)}</div>
      ${q.media && q.media.length && !hideCw ? `<span class="cl-mini-chip">🖼 ${q.media.length}</span>` : ""}
    </div>`;
  }

  function historyHtml(post) {
    const entries = [...(post.history || []), { at: post.editedAt || post.time, text: post.text, current: true }];
    return `<div class="cl-history"><b>수정 이력 (${entries.length - 1}회 수정)</b>${entries.map((h) => `<div class="cl-history-item ${h.current ? "is-current" : ""}"><span>${esc(h.at)}${h.current ? " · 현재" : ""}</span><div>${textHtml(h.text)}</div></div>`).join("")}</div>`;
  }

  /* mode: compact(요약 없음) · card(요약 2줄) · roomy(전체) · focus(리더) */
  function postBody(post, mode) {
    const acc = account(post.accountId);
    const f = matchFilter(post, acc, mode === "focus" ? "thread" : filterContext(cs.timelineId));
    if (f && !cs.revealed.has(`${post.id}:filter`)) {
      return `<div class="cl-filtered">🛡️ 필터됨: “${esc(f.title)}” <small>(${esc(f.keywords.join(", "))})</small> <button type="button" class="tt-linklike" data-cl-reveal="${esc(post.id)}:filter">그래도 보기</button></div>`;
    }
    let html = "";
    const cwOpen = !post.cw || cs.revealed.has(`${post.id}:cw`);
    if (post.cw) html += `<div class="cl-cw"><span>⚠ ${esc(post.cw)}</span><button type="button" class="cl-cw-btn" data-cl-reveal="${esc(post.id)}:cw" aria-expanded="${cwOpen}">${cwOpen ? "숨기기" : "내용 보기"}</button></div>`;
    if (!cwOpen) return html;
    const translated = cs.translated.has(post.id) && post.translation;
    const clamp = mode === "compact" ? "clamp-2" : (mode === "card" ? "clamp-5" : "");
    html += `<div class="cl-post-text ${clamp} ${mode === "focus" ? "is-focus" : ""}">${textHtml(translated ? post.translation : post.text)}</div>`;
    if (translated) {
      html += `<div class="cl-translated">🌐 ${esc(LANG[post.lang] || post.lang)}에서 번역됨 · <button type="button" class="tt-linklike" data-cl-translate="${esc(post.id)}">원문 보기</button></div>`;
    } else if (post.translation && mode !== "compact" && acc && acc.proto === "ap" && supports(acc, "translate")) {
      html += `<button type="button" class="tt-linklike cl-translate-link" data-cl-translate="${esc(post.id)}">🌐 번역하기</button>`;
    }
    if (cs.historyOpen.has(post.id) && post.edited) html += historyHtml(post);
    if (mode === "compact") {
      const chips = [];
      if (post.media && post.media.length) chips.push(`🖼 ${post.media.length}${post.sensitive ? " · 민감" : ""}`);
      if (post.poll) chips.push("📊 투표");
      if (post.quoteOf) chips.push("❝ 인용");
      return html + (chips.length ? `<div class="cl-chip-line">${chips.map((c) => `<span class="cl-mini-chip">${c}</span>`).join("")}</div>` : "");
    }
    if (post.media && post.media.length) html += mediaHtml(post);
    if (post.poll) html += pollHtml(post);
    if (post.quoteOf) html += quoteHtml(post, mode !== "card");
    html += linkCard(post.card, mode !== "card");
    if (mode !== "focus") html += matchedTagsLine(post);
    return html;
  }

  function actionBar(post, withLabels) {
    if (post.notifOnly) return "";
    const acc = account(post.accountId);
    if (post.scheduled) {
      return `<div class="cl-actions">
        <span class="cl-meta-chip">🗓️ ${esc(post.scheduled.at)} 게시 예정</span><span class="cl-act-sep"></span>
        <button type="button" class="cl-act" data-cl-act="sched-edit" data-cl-id="${esc(post.id)}" ${canWrite(acc, "post") ? "" : "disabled"} title="PUT /api/v1/scheduled_statuses/:id">🕘 <span>시간 변경</span></button>
        <button type="button" class="cl-act" data-cl-act="sched-cancel" data-cl-id="${esc(post.id)}" ${canWrite(acc, "post") ? "" : "disabled"} title="DELETE /api/v1/scheduled_statuses/:id">✕ <span>예약 취소</span></button>
      </div>`;
    }
    const proto = acc ? acc.proto : "ap";
    const btn = (act, icon, label, on, count, tip, kind) => {
      const disabled = kind && !canWrite(acc, kind);
      return `<button type="button" class="cl-act ${on ? "on" : ""}" data-cl-act="${act}" data-cl-id="${esc(post.id)}" ${disabled ? "disabled" : ""} title="${esc(disabled ? `읽기 전용 연결 계정 (${SCOPE_FOR[kind]} 없음)` : tip)}">${icon}${withLabels ? ` <span>${label}</span>` : ""}${count !== undefined ? ` <b>${count}</b>` : ""}</button>`;
    };
    const restricted = (post.visibility === "private" || post.visibility === "direct") && !post.own;
    const quoteOk = proto === "at" || supports(acc, "quote");
    const rel = kbItemFor(post);
    return `<div class="cl-actions">
      ${btn("reply", "💬", "답글", false, post.counts.replies, "답글 (r)", "reply")}
      ${restricted
        ? `<span class="cl-act" title="팔로워 전용 · 개인 메시지는 다른 사람이 부스트할 수 없습니다">🔒</span>`
        : btn("boost", "🔁", proto === "at" ? "리포스트" : "부스트", post.boosted, post.counts.boosts, proto === "at" ? "리포스트 (b) · app.bsky.feed.repost" : "부스트 (b) · POST /api/v1/statuses/:id/reblog", "boost")}
      ${btn("fav", "★", "좋아요", post.favourited, post.counts.favs, "좋아요 (f)", "favourite")}
      ${proto === "ap" ? btn("bookmark", "🔖", "북마크", post.bookmarked, undefined, "북마크 · POST /api/v1/statuses/:id/bookmark", "bookmark") : ""}
      ${quoteOk && !restricted ? btn("quote", "❝", "인용", false, undefined, "인용 (q)", "quote") : ""}
      <button type="button" class="cl-act" data-cl-more="${esc(post.id)}" popovertarget="pop-post-actions" title="더 보기: 링크 · 번역 · 대화 뮤트 · 뮤트 · 차단 · 신고 · 고정 · 수정 · 삭제" aria-label="게시물 추가 작업">⋯</button>
      <span class="cl-act-sep"></span>
      ${rel ? `<button type="button" class="cl-act kb" data-cl-act="open-kb" data-cl-id="${esc(post.id)}" title="지식베이스 탭에서 수집 항목 열기 (2)">📚${withLabels ? " <span>지식베이스에서 열기</span>" : ""}</button>`
        : `<button type="button" class="cl-act kb" data-cl-act="send" data-cl-id="${esc(post.id)}" title="지식베이스 수집함으로 보내기 (s) — 엔티티는 적재 시 생성">📥${withLabels ? " <span>수집함 보내기</span>" : ""}</button>`}
      ${rel && rel.item.ingestState === "linked" ? "" : `<button type="button" class="cl-act kb" data-cl-act="ingest" data-cl-id="${esc(post.id)}" title="보내고 바로 적재 (i)">⚡${withLabels ? " <span>보내고 적재</span>" : ""}</button>`}
    </div>`;
  }

  function flatSummary(post) {
    const acc = account(post.accountId);
    const f = matchFilter(post, acc, filterContext(cs.timelineId));
    if (f && !cs.revealed.has(`${post.id}:filter`)) return `🛡️ 필터됨: ${f.title}`;
    if (post.cw && !cs.revealed.has(`${post.id}:cw`)) return `⚠ ${post.cw}`;
    return post.text;
  }

  function renderRow(post) {
    const style = state.listStyle;
    const isSel = cs.selectedPostId === post.id;
    const proto = postProto(post);
    if (post.notifOnly) {
      const n = post.notif;
      const [icon, tail] = NOTIF[n.kind] || ["🔔", ""];
      const by = Array.isArray(n.by) ? n.by : [n.by];
      const target = n.target ? byId(n.target) : null;
      if (style === "list") {
        const who = `${by.slice(0, 2).map((h) => String(h).split("@")[1]).join(", ")}${(n.total || by.length) > 2 ? ` 외 ${(n.total || by.length) - 2}명` : ""}`;
        return `<div class="tt-row-wrap ${post.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-cl-post="${esc(post.id)}">
          <div class="tt-flat-row"><span class="tt-proto-dot">${protoDot(proto)}</span><span class="cl-notif-icon">${icon}</span>
            <div class="tt-flat-main"><span class="tt-flat-title">${esc(who)}</span><span class="tt-flat-excerpt">${esc(tail)}${target ? ` — ${esc(target.text)}` : ""}</span></div>
            <div class="tt-flat-right"><span class="tt-flat-time">${esc(post.time)}</span></div></div>
        </div>`;
      }
      return `<article class="tt-card-row cl-post cl-notif-group ${post.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-cl-post="${esc(post.id)}">
        ${notifLine(post)}
        <div class="cl-avatar-row">${by.map((h) => `<button type="button" class="cl-avatar is-small proto-${proto}" data-cl-profile="${esc(h)}" title="${esc(h)}">${initial(String(h).split("@")[1])}</button>`).join("")}${(n.total || by.length) > by.length ? `<span class="cl-more-count">+${(n.total || by.length) - by.length}</span>` : ""}</div>
        ${target && style !== "compact" ? `<div class="cl-quote" data-cl-open="${esc(target.id)}"><div class="cl-post-text clamp-2">${textHtml(target.text)}</div></div>` : ""}
      </article>`;
    }
    if (style === "list") {
      const icons = [post.media && post.media.length ? "🖼" : "", post.poll ? "📊" : "", post.quoteOf ? "❝" : "", post.edited ? "✏️" : ""].filter(Boolean).join("");
      return `<div class="tt-row-wrap ${post.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-cl-post="${esc(post.id)}">
        <div class="tt-flat-row">
          <span class="tt-proto-dot" title="${proto === "at" ? "AT Protocol" : "ActivityPub"}">${protoDot(proto)}</span>
          ${post.notif ? `<span class="cl-notif-icon" title="${esc(post.notif.kind)}">${(NOTIF[post.notif.kind] || ["🔔"])[0]}</span>` : ""}
          ${post.rebloggedBy ? `<span class="cl-notif-icon" title="${esc(post.rebloggedBy.name)} 님이 부스트">🔁</span>` : ""}
          ${post.scheduled ? `<span class="cl-notif-icon" title="예약 게시">🗓️</span>` : ""}
          ${kbBadge(post)}
          <div class="tt-flat-main">
            <span class="tt-flat-title">${esc(post.author)}</span>
            <span class="tt-flat-author">${esc(post.handle)}</span>
            <span class="tt-flat-excerpt">${icons ? `${icons} ` : ""}— ${esc(flatSummary(post))}</span>
          </div>
          <div class="tt-flat-right">
            ${post.scheduled ? `<span class="cl-counts">${esc(post.scheduled.at)}</span>` : `<span class="cl-counts">💬${post.counts.replies} 🔁${post.counts.boosts} ★${post.counts.favs}</span>`}
            <span class="tt-flat-time">${esc(post.time)}</span>
            ${post.url ? `<a class="tt-ext-link" href="${esc(post.url)}" target="_blank" rel="noopener noreferrer" title="원격 서버에서 열기">→</a>` : ""}
          </div>
        </div>
      </div>`;
    }
    return `<article class="tt-card-row cl-post ${post.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-cl-post="${esc(post.id)}">
      ${boostLine(post)}
      ${notifLine(post)}
      ${postHead(post)}
      ${postBody(post, style)}
      ${actionBar(post, style === "roomy")}
    </article>`;
  }

  /* ── 리더: 스레드 · 프로필 ── */
  function threadItem(post, role) {
    return `<article class="cl-thread-item is-${role}" ${role === "focus" ? "" : `data-cl-post="${esc(post.id)}"`}>
      ${role === "focus" ? `${boostLine(post)}${notifLine(post)}` : ""}
      ${postHead(post)}
      ${postBody(post, role === "focus" ? "focus" : "card")}
    </article>`;
  }

  function kbPanel(post) {
    const rel = kbItemFor(post);
    const tagChips = (post.hashtags || []).map((t) => {
      const m = entityMatch(t);
      return `<button type="button" class="tt-entity-chip candidate" data-cl-search-tag="${esc(t)}" title="${m ? `테마 DB 엔티티 [[${esc(m.name)}]] 와 이름 일치 — 적재 시 즉시 연계` : "수집 태그 — 적재 시 엔티티로 승격"}">#${esc(t)}${m ? " <small style='color:var(--accent)'>[일치]</small>" : ""}</button>`;
    }).join("") || `<span style="color:var(--muted)">해시태그 없음</span>`;

    if (rel && rel.item.ingestState === "linked" && rel.item.linkedDoc) {
      const ents = api.getLinkedDocEntities(rel.item);
      return `<section class="cl-kb-panel is-linked" aria-label="지식베이스 연계 상태">
        <div class="cl-kb-title">🔗 지식베이스 적재 완료 — <code>${esc(rel.item.linkedDoc.docId)}</code></div>
        <p>${rel.via === "url" ? `동일 URL(canonical_url)의 문서가 이미 적재되어 있어 중복 수집을 생략합니다 (원 수집 경로: <code>${esc(rel.item.format)}</code>).` : "이 게시물에서 수집·적재된 문서입니다."} 적재 시 생성·연계된 엔티티:</p>
        <div class="cl-chip-row">${ents.map((e) => `<span class="tt-entity-chip resolved">[[${esc(e.name)}]] <small style="opacity:0.8">(${esc(e.type || "Concept")})</small></span>`).join("")}</div>
        <div class="cl-kb-actions"><button type="button" class="tt-btn" data-cl-act="open-kb" data-cl-id="${esc(post.id)}">📚 지식베이스 탭에서 열기</button></div>
      </section>`;
    }
    if (rel) {
      return `<section class="cl-kb-panel is-inbox" aria-label="지식베이스 연계 상태">
        <div class="cl-kb-title">📥 지식베이스 수집함에 있음 · ○ 미적재 (<code>${esc(rel.item.id)}</code>)</div>
        <p>${rel.via === "url" ? "동일 URL의 수집 항목이 이미 있어 새로 보내지 않습니다. " : ""}수집 태그는 아직 <b>#태그</b>이며, 적재(i)를 실행해야 테마 DB 엔티티가 생성됩니다.</p>
        <div class="cl-chip-row">${tagChips}</div>
        <div class="cl-kb-actions">
          <button type="button" class="tt-btn tt-btn-pri" data-cl-act="ingest" data-cl-id="${esc(post.id)}">⚡ 지금 적재 (i)</button>
          <button type="button" class="tt-btn" data-cl-act="open-kb" data-cl-id="${esc(post.id)}">📚 지식베이스 탭에서 열기</button>
        </div>
      </section>`;
    }
    return `<section class="cl-kb-panel" aria-label="지식베이스 연계 상태">
      <div class="cl-kb-title">지식베이스에 아직 없음</div>
      <p><b>수집함 보내기(s)</b>는 이 게시물을 수집 원문으로 저장합니다. 해시태그는 <b>#수집 태그</b>로 보존되고, <b>엔티티는 적재 시에 생성</b>됩니다. 연관도 힌트: <b>+${affinityScore(post)}</b> (테마 엔티티 이름 일치 기반)</p>
      <div class="cl-chip-row">${tagChips}</div>
      <div class="cl-kb-actions">
        <button type="button" class="tt-btn" data-cl-act="send" data-cl-id="${esc(post.id)}">📥 수집함 보내기 (s)</button>
        <button type="button" class="tt-btn tt-btn-pri" data-cl-act="ingest" data-cl-id="${esc(post.id)}">⚡ 보내고 바로 적재 (i)</button>
      </div>
    </section>`;
  }

  function buildReader(post) {
    const acc = account(post.accountId);
    if (!acc) return "";
    const ancestors = [];
    let cur = post.replyTo ? byId(post.replyTo) : null;
    while (cur) { ancestors.unshift(cur); cur = cur.replyTo ? byId(cur.replyTo) : null; }
    const replies = posts().filter((p) => p.replyTo === post.id).sort((a, b) => a.timestamp - b.timestamp);
    const threadApi = acc.proto === "ap"
      ? `GET ${acc.server}/api/v1/statuses/${post.remoteId || "{id}"}/context`
      : `GET ${acc.server}/xrpc/app.bsky.feed.getPostThread?uri=${post.uri}`;
    const fontCls = prefs.readerFont === "serif" ? "font-serif" : "";
    return `<div class="tt-article-card cl-reader">
      <header class="tt-art-header">
        <div class="tt-art-title-row">
          <h2 class="tt-art-title">${post.scheduled ? "예약 게시물" : `스레드 · ${esc(post.author)}`}</h2>
          ${post.url ? `<a class="tt-btn" href="${esc(post.url)}" target="_blank" rel="noopener noreferrer" title="원격 서버의 원본 게시물 열기">원격 서버에서 열기 ↗</a>` : ""}
        </div>
        <div class="tt-art-meta">
          <span>${protoDot(acc.proto)} 연결 계정 <code>${esc(acc.handle)}</code> 경유 · <code>${esc(post.scheduled ? `GET ${acc.server}/api/v1/scheduled_statuses/${post.scheduled.id}` : threadApi)}</code></span>
        </div>
      </header>
      <div class="cl-thread ${fontCls}" style="--read-fs:${prefs.readerFontSize}px">
        ${ancestors.map((p) => threadItem(p, "ancestor")).join("")}
        ${threadItem(post, "focus")}
        ${post.scheduled ? "" : `<dl class="cl-meta-grid">
          <dt>${acc.proto === "ap" ? "AP 객체 URI" : "AT URI"}</dt><dd><code>${esc(post.uri || "-")}</code></dd>
          <dt>${acc.proto === "ap" ? "연결 서버 status id" : "레코드 CID"}</dt><dd><code>${esc(acc.proto === "ap" ? (post.remoteId || "-") : (post.cid || "-"))}</code></dd>
          ${acc.proto === "ap" && post.own && post.quotePolicy ? `<dt>인용 허용 범위</dt><dd>${esc({ public: "누구나", followers: "팔로워만", nobody: "나만 (인용 불가)" }[post.quotePolicy] || post.quotePolicy)}</dd>` : ""}
        </dl>`}
        ${actionBar(post, true)}
        ${post.scheduled ? "" : kbPanel(post)}
        ${replies.length ? `<div class="cl-replies-hdr">답글 ${replies.length}개</div>${replies.map((p) => threadItem(p, "reply")).join("")}` : ""}
      </div>
    </div>`;
  }

  function profileAccount(handle) {
    const p = posts().find((x) => x.handle === handle && !x.notifOnly);
    return p ? account(p.accountId) : (account(cs.accountId) || apAccounts()[0] || null);
  }

  function profileOf(handle) {
    const t = theme();
    if (!t.profiles) t.profiles = {};
    if (!t.profiles[handle]) {
      const own = accounts().find((a) => a.handle === handle);
      const sample = posts().find((p) => p.handle === handle && !p.notifOnly);
      t.profiles[handle] = {
        name: own ? own.displayName : (sample ? sample.author : (String(handle).split("@")[1] || handle)),
        bio: own ? `${theme().label} 테마의 연결 계정 (${own.software})` : "", fields: [],
        followers: null, following: null, statuses: posts().filter((p) => p.handle === handle && !p.notifOnly).length,
        joined: "", bot: false, featuredTags: [], pinned: [], synthesized: true,
        rel: { following: false, notify: false, showReblogs: true, muted: false, blocked: false, note: "", lists: [] }
      };
    }
    return t.profiles[handle];
  }

  function buildProfile(handle) {
    const pr = profileOf(handle);
    const acc = profileAccount(handle);
    const proto = acc ? acc.proto : "ap";
    const own = isOwnHandle(handle);
    const rel = pr.rel;
    const muted = acc && (acc.mutes || []).some((m) => m.handle === handle);
    const blocked = acc && (acc.blocks || []).some((b) => b.handle === handle);
    const pinned = [...(pr.pinned || []), ...posts().filter((p) => p.handle === handle && p.pinned && !(pr.pinned || []).includes(p.id)).map((p) => p.id)].map(byId).filter(Boolean);
    const recent = posts().filter((p) => p.handle === handle && !p.notifOnly && !p.scheduled && !pinned.includes(p)).sort((a, b) => b.timestamp - a.timestamp).slice(0, 5);
    const num = (n) => (n === null || n === undefined ? "—" : Number(n).toLocaleString("ko-KR"));
    const rb = (key, label, on, kind, tip) => `<button type="button" class="tt-btn ${on ? "is-on" : ""}" data-cl-rel="${key}" data-cl-handle="${esc(handle)}" ${kind && !canWrite(acc, kind) ? "disabled" : ""} title="${esc(tip || "")}">${label}</button>`;
    const lookup = proto === "ap" ? `GET ${acc ? acc.server : ""}/api/v1/accounts/lookup?acct=${handle.replace(/^@/, "")}` : `GET ${acc ? acc.server : ""}/xrpc/app.bsky.actor.getProfile?actor=${handle.replace(/^@/, "")}`;
    const mini = (p) => `<div class="cl-mini-post" data-cl-open="${esc(p.id)}" role="link" tabindex="0"><span class="tt-card-time">${esc(p.time)}</span> ${p.pinned || (pr.pinned || []).includes(p.id) ? "📌 " : ""}${esc(p.cw ? `⚠ ${p.cw}` : p.text)}</div>`;

    return `<div class="tt-article-card cl-reader cl-profile">
      <div class="cl-profile-top">
        <button type="button" class="tt-btn" data-cl-back="1">← 스레드로</button>
        <code>${esc(lookup)}</code>
      </div>
      <div class="cl-profile-head">
        <span class="cl-avatar is-large proto-${proto}" aria-hidden="true">${initial(pr.name)}</span>
        <div>
          <h2 class="tt-art-title">${esc(pr.name)} ${pr.bot ? `<span class="cl-meta-chip">🤖 봇</span>` : ""}</h2>
          <div class="cl-handle">${esc(handle)}${pr.joined ? ` · ${esc(pr.joined)} 가입` : ""}</div>
        </div>
      </div>
      ${pr.bio ? `<p class="cl-profile-bio">${textHtml(pr.bio)}</p>` : ""}
      ${(pr.fields || []).length ? `<dl class="cl-profile-fields">${pr.fields.map((f) => `<dt>${esc(f.name)}</dt><dd class="${f.verified ? "verified" : ""}">${f.verified ? "✓ " : ""}${esc(f.value)}</dd>`).join("")}</dl>` : ""}
      <div class="cl-profile-stats"><span><b>${num(pr.statuses)}</b> 게시물</span><span><b>${num(pr.following)}</b> 팔로잉</span><span><b>${num(pr.followers)}</b> 팔로워</span></div>
      ${own ? `<div class="cl-rel-bar"><span class="cl-meta-chip">내 연결 계정</span><button type="button" class="tt-btn" data-cl-rel="edit-profile" data-cl-handle="${esc(handle)}" ${canWrite(acc, "note") ? "" : "disabled"}>프로필 편집</button></div>`
        : `<div class="cl-rel-bar">
          ${rb("follow", rel.following ? "팔로잉 ✓" : "+ 팔로우", rel.following, "follow", proto === "ap" ? "POST /api/v1/accounts/:id/follow · unfollow" : "app.bsky.graph.follow")}
          ${proto === "ap" ? rb("notify", rel.notify ? "🔔 새 글 알림 켜짐" : "🔕 새 글 알림", rel.notify, "follow", "follow { notify }") : ""}
          ${proto === "ap" ? rb("reblogs", rel.showReblogs ? "🔁 부스트 표시" : "🔁 부스트 숨김", !rel.showReblogs, "follow", "follow { reblogs }") : ""}
          ${proto === "ap" ? rb("lists", "📋 리스트", cs.listPicker, "list", "POST /api/v1/lists/:id/accounts") : ""}
          ${proto === "ap" ? rb("note", "📝 메모", !!rel.note, "note", "POST /api/v1/accounts/:id/note") : ""}
          ${rb("mute", muted ? "🔊 뮤트 해제" : "🔇 뮤트", muted, "mute")}
          ${rb("block", blocked ? "차단 해제" : "⛔ 차단", blocked, "block")}
          ${rb("report", "🚩 신고", false, "report")}
        </div>`}
      ${cs.listPicker && acc && acc.proto === "ap" ? `<div class="cl-list-picker">${(acc.lists || []).length ? acc.lists.map((l) => `<button type="button" class="tt-entity-chip ${rel.lists.includes(l.id) ? "resolved" : "candidate"}" data-cl-list-toggle="${esc(l.id)}" data-cl-handle="${esc(handle)}">${rel.lists.includes(l.id) ? "✓ " : "+ "}${esc(l.title)}</button>`).join("") : `<span class="cl-handle">리스트가 없습니다 — 필터·뮤트·차단 화면에서 새 리스트를 만드세요.</span>`}</div>` : ""}
      ${cs.noteEdit || rel.note ? `<label class="cl-note"><span>📝 개인 메모 (나만 보임)</span><textarea data-cl-note="${esc(handle)}" rows="2" ${canWrite(acc, "note") ? "" : "disabled"}>${esc(rel.note || "")}</textarea></label>` : ""}
      ${(pr.featuredTags || []).length ? `<div class="cl-chip-row"><span class="cl-handle">추천 해시태그</span>${pr.featuredTags.map((t) => `<button type="button" class="tt-entity-chip candidate" data-cl-tag-open="${esc(t)}">#${esc(t)}</button>`).join("")}</div>` : ""}
      ${pinned.length ? `<div class="cl-replies-hdr">고정된 게시물</div>${pinned.map(mini).join("")}` : ""}
      <div class="cl-replies-hdr">최근 게시물</div>
      ${recent.length ? recent.map(mini).join("") : `<div class="cl-handle">표시할 게시물이 없습니다.</div>`}
      ${own ? "" : `<div class="cl-kb-panel"><div class="cl-kb-title">지식베이스 연계</div><p>이 계정의 게시물을 연결 계정 경유로 정기 수집합니다 (${proto === "ap" ? "GET /api/v1/accounts/:id/statuses" : "app.bsky.feed.getAuthorFeed"}). 엔티티는 적재 시에 생성됩니다.</p><div class="cl-kb-actions"><button type="button" class="tt-btn" data-cl-rel="register" data-cl-handle="${esc(handle)}">⇢ 지식베이스 수집 대상 등록</button></div></div>`}
    </div>`;
  }

  /* ── 트리 ── */
  function renderTree() {
    const accs = accounts();
    const host = document.getElementById("cl-tree");
    const sentCnt = posts().filter((p) => p.kbItemId).length;
    host.innerHTML = `<div class="tt-tree-hdr"><span>연결 서버 계정</span><button type="button" class="tt-mini-btn" data-cl-connect="1" title="Mastodon API 서버 또는 AT Protocol PDS 계정 연결">+ 연결</button></div>
      ${accs.length ? accs.map((acc) => {
        const children = timelinesOf(acc).map((tl) => {
          const cnt = tl.kind === "favourites" || tl.kind === "bookmarks" || tl.kind === "manage" || tl.kind === "explore"
            ? 0
            : posts().filter((p) => p.accountId === acc.id && (p.timelines || []).includes(tl.id) && p.unread).length;
          const act = cs.accountId === acc.id && cs.timelineId === tl.id && !cs.profile ? "active" : "";
          return `<button type="button" class="tt-tree-item ${act}" data-cl-tl="${esc(acc.id)}" data-cl-tl-id="${esc(tl.id)}" title="${esc(tl.api)}"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(tl.icon)} ${esc(tl.label)}</span><span class="tt-count-pill ${cnt > 0 ? "fresh" : ""}">${cnt || ""}</span></button>`;
        }).join("");
        const missing = (acc.missingFeatures || []).length;
        return `<details class="tt-folder" open>
          <summary><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><span class="tt-folder-marker"></span>${protoDot(acc.proto)} ${esc(acc.handle)}</span><span class="tt-acct-dot status-${esc(acc.status || "ok")}" title="${esc(acc.statusNote || "")}"></span></summary>
          <div class="cl-acct-meta" title="${esc(acc.auth)} · scope: ${esc(acc.scopes.join(" "))}">${esc(acc.software)}${acc.serverVersion ? ` ${esc(acc.serverVersion)}` : ""} · ${esc(acc.auth.split(" (")[0])}${acc.canPublish ? "" : " · <b>읽기 전용</b>"}${missing ? ` · <span title="${esc(acc.missingFeatures.join(", "))}">기준 기능 ${missing}개 미제공</span>` : ""}</div>
          <div class="tt-tree-children">${children}</div>
        </details>`;
      }).join("") : `<div class="cl-tree-note">이 테마에 연결된 서버 계정이 없습니다. <b>+ 연결</b>로 Mastodon API 서버 또는 AT Protocol PDS 계정을 연결하세요.</div>`}
      <div class="tt-tree-hdr"><span>지식베이스 연계</span></div>
      <div class="tt-tree-children" style="padding-left:0">
        <button type="button" class="tt-tree-item ${cs.timelineId === "sent" ? "active" : ""}" data-cl-tl="" data-cl-tl-id="sent"><span>📥 수집함으로 보낸 게시물</span><span class="tt-count-pill">${sentCnt}</span></button>
        <button type="button" class="tt-tree-item ${cs.timelineId === "features" ? "active" : ""}" data-cl-tl="" data-cl-tl-id="features" title="ActivityPub 클라이언트 기능 기준표"><span>📋 기능 기준 (Mastodon 5.0+)</span></button>
      </div>
      <p class="cl-tree-note">ⓘ Claire 는 연합 서버를 직접 운영하지 않습니다. 각 계정의 서버 API(Mastodon Client API · AT XRPC)로 읽고 쓰며, 접근 토큰은 Claire 서버에만 암호화 보관됩니다.</p>`;
  }

  function timelineCall(acc, tl) {
    if (acc.proto === "ap") return tl.api.replace(/^(GET|POST) \//, `$1 ${acc.server}/`);
    return `GET ${acc.server}/xrpc/${tl.api}`;
  }

  function renderStreamHead() {
    const head = document.getElementById("cl-stream-head");
    if (cs.timelineId === "sent") {
      head.innerHTML = `<div class="tt-stream-head-l"><b>${VIRTUAL.sent}</b><code>로컬 · 지식베이스 수집함 연계</code></div>`;
      return;
    }
    if (cs.timelineId === "features") {
      head.innerHTML = `<div class="tt-stream-head-l"><b>${VIRTUAL.features}</b><code>연결 서버가 제공하지 않는 기능만 비활성</code></div>`;
      return;
    }
    const acc = account(cs.accountId);
    if (cs.timelineId === "search") {
      head.innerHTML = `<div class="tt-stream-head-l"><b>🔎 '${esc(cs.serverQuery)}'</b><code>${acc ? `GET ${esc(acc.server)}/api/v2/search?q=${esc(encodeURIComponent(cs.serverQuery))}&resolve=true` : ""}</code></div>
        <div class="tt-stream-head-r"><button type="button" class="tt-btn" data-cl-search-close="1">✕ 검색 닫기</button></div>`;
      return;
    }
    const tl = currentTimeline();
    if (!acc || !tl) { head.innerHTML = ""; return; }
    const registered = theme().feeds.some((f) => f.via === acc.id && f.timelineId === tl.id);
    const live = acc.proto === "ap" && tl.stream ? `<span class="cl-live" title="GET ${esc(acc.server)}/api/v1/streaming?stream=${esc(tl.stream)}">● 실시간</span>` : "";
    let extra = "";
    if (tl.kind === "notifications" && acc.proto === "ap") {
      extra = `<div class="tt-seg" role="group" aria-label="알림 종류">
        <button type="button" class="tt-seg-btn ${cs.notifFilter === "all" ? "active" : ""}" data-cl-notif-filter="all">전체</button>
        <button type="button" class="tt-seg-btn ${cs.notifFilter === "mention" ? "active" : ""}" data-cl-notif-filter="mention">멘션</button>
      </div>`;
    } else if (tl.kind === "explore") {
      extra = `<div class="tt-seg" role="group" aria-label="탐색 분류">
        ${[["statuses", "게시물"], ["tags", "해시태그"], ["links", "뉴스"]].map(([k, l]) => `<button type="button" class="tt-seg-btn ${cs.exploreTab === k ? "active" : ""}" data-cl-explore="${k}">${l}</button>`).join("")}
      </div>`;
    }
    head.innerHTML = `<div class="tt-stream-head-l"><b>${esc(tl.icon)} ${esc(tl.label)}</b>${live}<code title="연결 서버 API (Claire 서버 프록시 경유)">${esc(timelineCall(acc, tl))}</code></div>
      <div class="tt-stream-head-r">
        ${extra}
        ${tl.kind === "manage" || tl.kind === "explore" ? "" : `<button type="button" class="tt-btn" data-cl-refresh="1" title="연결 서버에서 새 게시물 가져오기">↻</button>`}
        ${tl.collectable ? (registered
          ? `<span class="tt-link-dot linked" title="이 타임라인은 지식베이스 정기 수집 대상입니다">✓ 수집 대상</span>`
          : `<button type="button" class="tt-btn" data-cl-register="1" title="이 타임라인을 지식베이스 정기 수집 대상으로 등록 (연결 계정 경유)">⇢ 수집 대상 등록</button>`) : ""}
      </div>`;
  }

  /* ── 특수 화면: 알림 요청 · 탐색 · 검색 · 관리 · 기능 기준 ── */
  function renderNotifRequests(acc) {
    if (!acc || acc.proto !== "ap" || !supports(acc, "notifRequests") || !(acc.notifRequests || []).length) return "";
    const reqs = acc.notifRequests;
    return `<div class="cl-banner">
      <button type="button" class="cl-banner-head" data-cl-requests="1" aria-expanded="${cs.showRequests}">📨 알림 요청 ${reqs.length}건 <small>— 알림 정책에 따라 걸러진 계정의 알림 (GET /api/v1/notifications/requests)</small><span>${cs.showRequests ? "▾" : "▸"}</span></button>
      ${cs.showRequests ? reqs.map((r) => `<div class="cl-req-row">
        <div><button type="button" class="cl-name" data-cl-profile="${esc(r.handle)}"><b>${esc(r.handle)}</b></button> <span class="cl-meta-chip">${esc(r.reason)}</span> <span class="cl-handle">알림 ${r.count}건</span><div class="cl-handle">${esc(r.preview)}</div></div>
        <div class="cl-req-actions">
          <button type="button" class="tt-btn" data-cl-req="accept" data-cl-arg="${esc(r.id)}" ${canWrite(acc, "notifications") ? "" : "disabled"}>수락</button>
          <button type="button" class="tt-btn" data-cl-req="dismiss" data-cl-arg="${esc(r.id)}" ${canWrite(acc, "notifications") ? "" : "disabled"}>거절</button>
        </div>
      </div>`).join("") : ""}
    </div>`;
  }

  function renderExplore(acc) {
    const tr = (acc && acc.trends) || { statuses: [], tags: [], links: [] };
    if (cs.exploreTab === "tags") {
      const max = Math.max(1, ...tr.tags.flatMap((t) => t.history));
      return tr.tags.length ? tr.tags.map((t) => {
        const followed = (acc.followedTags || []).includes(t.name);
        return `<div class="cl-trend-row">
          <div class="cl-trend-main"><b>#${esc(t.name)}</b><span class="cl-handle">최근 3일 ${t.accounts}명이 사용</span></div>
          <div class="cl-spark" aria-hidden="true">${t.history.map((v) => `<span style="height:${Math.max(3, Math.round((v / max) * 24))}px"></span>`).join("")}</div>
          <button type="button" class="tt-btn ${followed ? "is-on" : ""}" data-cl-tag-follow="${esc(t.name)}" ${canWrite(acc, "follow") ? "" : "disabled"}>${followed ? "팔로잉 ✓" : "+ 팔로우"}</button>
          <button type="button" class="tt-btn" data-cl-tag-open="${esc(t.name)}">타임라인</button>
        </div>`;
      }).join("") : `<div class="cl-empty">트렌드 해시태그가 없습니다.</div>`;
    }
    if (cs.exploreTab === "links") {
      return tr.links.length ? tr.links.map((l, i) => `<div class="cl-trend-link">
        ${linkCard({ title: l.title, description: l.description, url: l.url, author: l.author }, false)}
        <div class="cl-trend-meta"><span class="cl-handle">${esc(l.provider)} · 최근 ${l.shares}명이 공유</span><button type="button" class="tt-btn" data-cl-link-kb="${i}">📥 지식베이스로 수집</button></div>
      </div>`).join("") : `<div class="cl-empty">트렌드 링크가 없습니다.</div>`;
    }
    const list = trendPosts(acc);
    return list.length ? list.map(renderRow).join("") : `<div class="cl-empty">트렌드 게시물이 없습니다.</div>`;
  }

  function renderSearch(r) {
    const acc = account(cs.accountId);
    return `<div class="cl-search">
      <div class="cl-section-hdr">계정</div>
      ${r.accounts.map((h) => { const pr = profileOf(h); return `<button type="button" class="cl-search-acct" data-cl-profile="${esc(h)}"><span class="cl-avatar is-small proto-${acc ? acc.proto : "ap"}" aria-hidden="true">${initial(pr.name)}</span><b>${esc(pr.name)}</b> <span class="cl-handle">${esc(h)}</span></button>`; }).join("")}
      ${r.remote ? `<button type="button" class="cl-search-acct" data-cl-resolve="${esc(r.remote)}"><span class="cl-avatar is-small" aria-hidden="true">?</span><b>${esc(r.remote)}</b> <span class="cl-handle">원격 계정 확인 (resolve=true · WebFinger 는 연결 서버가 수행)</span></button>` : ""}
      ${!r.accounts.length && !r.remote ? `<div class="cl-empty">일치하는 계정 없음</div>` : ""}
      <div class="cl-section-hdr">해시태그</div>
      <div class="cl-chip-row">${r.tags.length ? r.tags.map((t) => `<button type="button" class="tt-entity-chip candidate" data-cl-tag-open="${esc(t)}">#${esc(t)}</button>`).join("") : `<span class="cl-handle">일치하는 해시태그 없음</span>`}</div>
      <div class="cl-section-hdr">게시물 (${r.posts.length})</div>
    </div>
    ${r.posts.map(renderRow).join("")}`;
  }

  function renderManage(acc) {
    if (!acc) return "";
    const ro = !canWrite(acc, "filter");
    const ctxLabel = { home: "홈·리스트", notifications: "알림", public: "공개 타임라인", thread: "대화", account: "프로필" };
    const row = (main, btn) => `<div class="cl-manage-row"><div>${main}</div>${btn}</div>`;
    const policyLabels = [["forNotFollowing", "내가 팔로우하지 않는 계정"], ["forNotFollowers", "나를 팔로우하지 않는 계정"], ["forNewAccounts", "새로 만든 계정 (30일 이내)"], ["forPrivateMentions", "요청하지 않은 개인 멘션"], ["forLimitedAccounts", "서버가 제한한 계정"]];
    return `<div class="cl-manage">
      <p class="cl-tree-note">이 설정은 연결 서버 계정(${esc(acc.handle)})에 저장되어, 같은 계정을 쓰는 다른 Mastodon 앱에도 똑같이 적용됩니다.</p>
      <section><div class="cl-section-hdr">🛡️ 키워드 필터 <code>/api/v2/filters</code></div>
        ${(acc.filters || []).map((f) => row(`<b>${esc(f.title)}</b> ${f.keywords.map((k) => `<span class="cl-mini-chip">${esc(k)}</span>`).join("")}<div class="cl-handle">${f.context.map((c) => ctxLabel[c] || c).join(" · ")} — ${f.action === "hide" ? "완전히 숨김" : "경고와 함께 접기"}</div>`,
          `<button type="button" class="tt-btn" data-cl-mg="filter-del" data-cl-arg="${esc(f.id)}" ${ro ? "disabled" : ""}>삭제</button>`)).join("") || `<div class="cl-empty">필터 없음</div>`}
        <form class="cl-manage-form" data-cl-filter-form="1">
          <input type="text" name="keyword" class="tt-search" placeholder="키워드 (쉼표로 여러 개)" required ${ro ? "disabled" : ""}/>
          <select name="action" class="tt-btn" ${ro ? "disabled" : ""}><option value="warn">경고와 함께 접기</option><option value="hide">완전히 숨김</option></select>
          ${["home", "public", "notifications", "thread"].map((c) => `<label><input type="checkbox" name="ctx" value="${c}" ${c === "home" || c === "public" ? "checked" : ""} ${ro ? "disabled" : ""}/> ${ctxLabel[c]}</label>`).join("")}
          <button type="submit" class="tt-btn tt-btn-pri" ${ro ? "disabled" : ""}>+ 필터 추가</button>
        </form>
      </section>
      <section><div class="cl-section-hdr">🔇 뮤트한 계정 <code>/api/v1/mutes</code></div>
        ${(acc.mutes || []).map((m) => row(`<button type="button" class="cl-name" data-cl-profile="${esc(m.handle)}"><b>${esc(m.handle)}</b></button> <span class="cl-handle">${esc(m.until || "무기한")}</span>`, `<button type="button" class="tt-btn" data-cl-mg="unmute" data-cl-arg="${esc(m.handle)}" ${canWrite(acc, "mute") ? "" : "disabled"}>뮤트 해제</button>`)).join("") || `<div class="cl-empty">없음</div>`}
      </section>
      <section><div class="cl-section-hdr">⛔ 차단한 계정 <code>/api/v1/blocks</code></div>
        ${(acc.blocks || []).map((b) => row(`<b>${esc(b.handle)}</b>`, `<button type="button" class="tt-btn" data-cl-mg="unblock" data-cl-arg="${esc(b.handle)}" ${canWrite(acc, "block") ? "" : "disabled"}>차단 해제</button>`)).join("") || `<div class="cl-empty">없음</div>`}
      </section>
      <section><div class="cl-section-hdr">🌐 차단한 도메인 <code>/api/v1/domain_blocks</code></div>
        ${(acc.domainBlocks || []).map((d) => row(`<b>${esc(d)}</b> <span class="cl-handle">이 도메인의 게시물·알림·팔로워를 숨김</span>`, `<button type="button" class="tt-btn" data-cl-mg="undomain" data-cl-arg="${esc(d)}" ${canWrite(acc, "block") ? "" : "disabled"}>해제</button>`)).join("") || `<div class="cl-empty">없음</div>`}
      </section>
      <section><div class="cl-section-hdr"># 팔로우한 해시태그 <code>/api/v1/followed_tags</code></div>
        <div class="cl-chip-row">${(acc.followedTags || []).map((t) => `<span class="tt-entity-chip candidate">#${esc(t)} <button type="button" class="cl-chip-x" data-cl-mg="untag" data-cl-arg="${esc(t)}" ${canWrite(acc, "follow") ? "" : "disabled"} aria-label="#${esc(t)} 언팔로우">✕</button></span>`).join("") || `<span class="cl-handle">없음</span>`}</div>
      </section>
      <section><div class="cl-section-hdr">📋 리스트 <code>/api/v1/lists</code></div>
        ${(acc.lists || []).map((l) => row(`<b>${esc(l.title)}</b>`, `<button type="button" class="tt-btn" data-cl-mg="list-del" data-cl-arg="${esc(l.id)}" ${canWrite(acc, "list") ? "" : "disabled"}>삭제</button>`)).join("") || `<div class="cl-empty">없음</div>`}
        <button type="button" class="tt-btn" data-cl-mg="list-new" ${canWrite(acc, "list") ? "" : "disabled"}>+ 새 리스트</button>
      </section>
      ${supports(acc, "notifRequests") ? `<section><div class="cl-section-hdr">🔔 알림 정책 <code>PATCH /api/v2/notifications/policy</code></div>
        ${policyLabels.map(([k, l]) => row(l, `<select class="tt-btn" data-cl-policy="${k}" ${canWrite(acc, "notifications") ? "" : "disabled"}>${[["accept", "수락"], ["filter", "알림 요청으로 거르기"], ["drop", "무시"]].map(([v, t]) => `<option value="${v}" ${(acc.notifPolicy || {})[k] === v ? "selected" : ""}>${t}</option>`).join("")}</select>`)).join("")}
      </section>` : ""}
    </div>`;
  }

  function renderFeatures() {
    const acc = apAccounts()[0] || null;
    return `<div class="cl-features">
      <p class="cl-tree-note">ActivityPub 클라이언트 기능은 <b>Mastodon 5.0 이상이 제공하는 클라이언트 기능</b>을 기준으로 갖춥니다. Mastodon API 를 제공하는 다른 서버도 연결할 수 있으며, 그 서버가 제공하지 않는 기능만 비활성으로 표시합니다.
      ${acc ? `현재 테마의 기준 계정: <b>${esc(acc.handle)}</b> (${esc(acc.software)}${acc.serverVersion ? ` ${esc(acc.serverVersion)}` : ""})` : "이 테마에는 ActivityPub 연결 계정이 없습니다."}</p>
      ${FEATURES.map((g, gi) => `<section>
        <div class="cl-section-hdr">${esc(g.group)}</div>
        ${g.items.map((it, ii) => {
          const on = !it.feature || supports(acc, it.feature);
          return `<div class="cl-feature-row ${on ? "" : "is-off"}">
            <span class="cl-feature-state" title="${on ? "이 연결 서버에서 사용 가능" : "연결 서버가 제공하지 않음 — 비활성"}">${on ? "✓" : "⊘"}</span>
            <div class="cl-feature-main"><b>${esc(it.label)}</b><code>${esc(it.api)}</code></div>
            <button type="button" class="tt-btn" data-cl-demo="${gi}:${ii}" ${acc ? "" : "disabled"}>보기 →</button>
          </div>`;
        }).join("")}
      </section>`).join("")}
    </div>`;
  }

  function readerHelp() {
    if (cs.timelineId === "features") {
      return `<div class="cl-reader-help"><h3>기능 기준 보기</h3><p>각 행의 <b>보기 →</b>는 해당 기능이 프로토타입에서 동작하는 타임라인·게시물·프로필·작성 화면으로 이동합니다.</p><p>⊘ 표시는 현재 연결 서버가 그 기능을 제공하지 않는다는 뜻입니다. 연결은 유지되고 해당 버튼만 비활성으로 표시됩니다.</p></div>`;
    }
    const tl = currentTimeline();
    if (tl && tl.kind === "manage") {
      return `<div class="cl-reader-help"><h3>필터 · 뮤트 · 차단</h3><p>키워드 필터는 문맥(홈·공개 타임라인·알림·대화)별로 적용되며, <b>경고와 함께 접기</b>는 “필터됨” 표시 뒤에 내용을 숨기고 <b>완전히 숨김</b>은 목록에서 제외합니다.</p><p>뮤트·차단·도메인 차단된 계정의 게시물은 타임라인에서 사라지며, 알림 정책에 걸린 알림은 <b>알림 요청</b>으로 모입니다.</p></div>`;
    }
    return `<div style="padding:36px;color:var(--muted);font-size:13px">타임라인에서 게시물을 선택하세요.</div>`;
  }

  function renderStream() {
    const container = document.getElementById("cl-headlines");
    const acc = account(cs.accountId);
    const tl = currentTimeline();
    let html;
    let selectable = null;
    if (cs.timelineId === "features") {
      html = renderFeatures();
    } else if (cs.timelineId === "search") {
      const r = searchResults();
      html = renderSearch(r);
      selectable = r.posts;
    } else if (tl && tl.kind === "explore") {
      html = renderExplore(acc);
      selectable = cs.exploreTab === "statuses" ? trendPosts(acc) : null;
    } else if (tl && tl.kind === "manage") {
      html = renderManage(acc);
    } else {
      const vis = visiblePosts();
      selectable = vis;
      html = (tl && tl.kind === "notifications" ? renderNotifRequests(acc) : "")
        + (vis.length ? vis.map(renderRow).join("") : `<div class="cl-empty">${accounts().length ? "이 타임라인에 표시할 게시물이 없습니다." : "연결된 서버 계정이 없습니다. 좌측 <b>+ 연결</b>로 계정을 연결하세요."}</div>`);
    }
    if (selectable && (!cs.selectedPostId || !byId(cs.selectedPostId))) cs.selectedPostId = selectable[0] ? selectable[0].id : null;
    if (!selectable && !byId(cs.selectedPostId)) cs.selectedPostId = null;
    container.innerHTML = html;

    const reader = document.getElementById("cl-reader");
    const sel = byId(cs.selectedPostId);
    if (cs.profile) {
      reader.innerHTML = buildProfile(cs.profile);
    } else if (sel && sel.notifOnly) {
      const target = sel.notif.target ? byId(sel.notif.target) : null;
      const first = Array.isArray(sel.notif.by) ? sel.notif.by[0] : sel.notif.by;
      reader.innerHTML = target ? buildReader(target) : buildProfile(first);
    } else if (sel && (selectable || cs.timelineId === "features")) {
      reader.innerHTML = cs.timelineId === "features" ? readerHelp() : buildReader(sel);
    } else {
      reader.innerHTML = readerHelp();
    }

    const accs = accounts();
    const sentCnt = posts().filter((p) => p.kbItemId).length;
    document.getElementById("footer-stats").textContent = `Theme #${theme().id} · 연결 계정 ${accs.length}개 (${accs.map((a) => `${protoDot(a.proto)} ${a.server}`).join(", ") || "없음"}) · 게시물 ${posts().filter((p) => !p.notifOnly).length}건 · 📥 수집함 보냄 ${sentCnt}건`;
  }

  function render() {
    ensureSelection();
    const composeBtn = document.getElementById("btn-compose");
    if (composeBtn) {
      composeBtn.disabled = !api.publishAccounts().length;
      composeBtn.title = composeBtn.disabled ? "이 테마에는 글을 쓸 수 있는 연결 계정이 없습니다 (읽기 전용)" : "연결 계정으로 새 글 작성 (n)";
    }
    renderTree();
    renderStreamHead();
    renderStream();
  }

  /* ── 동작 ── */
  function selectPost(id) {
    cs.selectedPostId = id;
    cs.profile = null;
    const post = byId(id);
    if (post && prefs.autoMarkReadOnOpen) post.unread = false;
    api.renderAll();
  }

  function openProfile(handle) {
    if (!handle) return;
    cs.profile = handle;
    cs.listPicker = false;
    cs.noteEdit = false;
    const acc = profileAccount(handle);
    if (acc) {
      const lookup = acc.proto === "ap"
        ? apCall(acc, "GET", `/api/v1/accounts/lookup?acct=${handle.replace(/^@/, "")}`)
        : atCall(acc, `app.bsky.actor.getProfile?actor=${handle.replace(/^@/, "")}`);
      ticker(`👤 프로필 → ${lookup.method === "POST" ? "GET" : lookup.method} ${lookup.url}`);
    }
    api.renderAll();
  }

  function toggleAction(post, kind) {
    const acc = account(post.accountId);
    if (!guard(acc, kind, { boost: "부스트", favourite: "좋아요", bookmark: "북마크" }[kind])) return;
    const req = api.buildServerRequest(acc, { kind, undo: false, target: postTarget(post) });
    if (!req) { ticker("AT Protocol 연결 계정은 북마크 대신 수집함 보내기(s)를 사용하세요."); return; }
    const field = { boost: "boosted", favourite: "favourited", bookmark: "bookmarked" }[kind];
    const undo = !!post[field];
    const finalReq = undo ? api.buildServerRequest(acc, { kind, undo: true, target: postTarget(post) }) : req;
    post[field] = !undo;
    if (kind === "boost") post.counts.boosts += undo ? -1 : 1;
    if (kind === "favourite") post.counts.favs += undo ? -1 : 1;
    const label = { boost: acc.proto === "at" ? "🔁 리포스트" : "🔁 부스트", favourite: "★ 좋아요", bookmark: "🔖 북마크" }[kind];
    announce(`${label}${undo ? " 취소" : ""}`, finalReq);
    api.renderAll();
  }

  function votePoll(post, button) {
    const acc = account(post.accountId);
    if (!guard(acc, "vote", "투표")) return;
    const box = button ? button.closest("[data-cl-poll]") : null;
    const choices = box ? [...box.querySelectorAll("input:checked")].map((i) => Number(i.value)) : [];
    if (!choices.length) { ticker("투표할 선택지를 고르세요."); return; }
    choices.forEach((i) => { post.poll.options[i].votes += 1; });
    post.poll.voted = true;
    post.poll.ownVotes = choices;
    announce("📊 투표", apCall(acc, "POST", `/api/v1/polls/${post.poll.id}/votes`, { choices }));
    api.renderAll();
  }

  function toggleTranslate(post) {
    const acc = account(post.accountId);
    if (!post.translation || !needFeature(acc, "translate", "번역")) return;
    toggleKey(cs.translated, post.id);
    if (cs.translated.has(post.id)) announce("🌐 번역", apCall(acc, "POST", `/api/v1/statuses/${post.remoteId}/translate`, { lang: "ko" }));
    api.renderAll();
  }

  function setMute(handle, on) {
    const acc = profileAccount(handle);
    if (!guard(acc, "mute", "뮤트")) return;
    acc.mutes = acc.mutes || [];
    if (on) acc.mutes.push({ handle, until: "무기한" }); else acc.mutes = acc.mutes.filter((m) => m.handle !== handle);
    const req = acc.proto === "ap"
      ? apCall(acc, "POST", `/api/v1/accounts/{account_id}/${on ? "mute" : "unmute"}`, on ? { notifications: true, duration: 0 } : undefined)
      : atCall(acc, on ? "app.bsky.graph.muteActor" : "app.bsky.graph.unmuteActor", { actor: handle.replace(/^@/, "") });
    announce(on ? `🔇 ${handle} 뮤트` : `🔊 ${handle} 뮤트 해제`, req);
    api.renderAll();
  }

  function setBlock(handle, on) {
    const acc = profileAccount(handle);
    if (!guard(acc, "block", "차단")) return;
    if (on && !window.confirm(`${handle} 을(를) 차단할까요? 서로의 게시물이 보이지 않고 팔로우 관계가 끊어집니다.`)) return;
    acc.blocks = acc.blocks || [];
    if (on) { acc.blocks.push({ handle }); profileOf(handle).rel.following = false; } else acc.blocks = acc.blocks.filter((b) => b.handle !== handle);
    const req = acc.proto === "ap"
      ? apCall(acc, "POST", `/api/v1/accounts/{account_id}/${on ? "block" : "unblock"}`)
      : (on ? atCall(acc, "com.atproto.repo.createRecord", { repo: acc.did, collection: "app.bsky.graph.block", record: { $type: "app.bsky.graph.block", subject: "{did}", createdAt: new Date().toISOString() } })
        : atCall(acc, "com.atproto.repo.deleteRecord", { repo: acc.did, collection: "app.bsky.graph.block", rkey: "{rkey}" }));
    announce(on ? `⛔ ${handle} 차단` : `${handle} 차단 해제`, req);
    api.renderAll();
  }

  function blockDomain(handle) {
    const acc = profileAccount(handle);
    const domain = domainOf(handle);
    if (!domain || !guard(acc, "block", "도메인 차단")) return;
    if (!window.confirm(`${domain} 도메인 전체를 차단할까요? 이 서버의 게시물·알림이 모두 숨겨지고 그 서버의 팔로워가 제거됩니다.`)) return;
    acc.domainBlocks = acc.domainBlocks || [];
    if (!acc.domainBlocks.includes(domain)) acc.domainBlocks.push(domain);
    announce(`🌐 ${domain} 도메인 차단`, apCall(acc, "POST", "/api/v1/domain_blocks", { domain }));
    api.renderAll();
  }

  function addFilterFromPost(post) {
    const acc = account(post.accountId);
    if (!guard(acc, "filter", "필터")) return;
    const suggestion = (post.hashtags && post.hashtags[0]) || post.text.split(/\s+/).find((w) => w.length > 1) || "";
    const kw = window.prompt("숨기거나 경고할 키워드 (쉼표로 여러 개):", suggestion);
    if (!kw || !kw.trim()) return;
    addFilter(acc, kw, "warn", ["home", "public", "thread"]);
  }

  function addFilter(acc, keywordsRaw, action, context) {
    const keywords = keywordsRaw.split(",").map((s) => s.trim()).filter(Boolean);
    if (!keywords.length) return;
    const f = { id: `flt-${Date.now().toString(36)}`, title: `“${keywords[0]}” 필터`, keywords, context, action };
    acc.filters = acc.filters || [];
    acc.filters.push(f);
    announce("🛡️ 필터 추가", apCall(acc, "POST", "/api/v2/filters", { title: f.title, context, filter_action: action, keywords_attributes: keywords.map((k) => ({ keyword: k, whole_word: true })) }));
    api.renderAll();
  }

  function deletePost(post, redraft) {
    const acc = account(post.accountId);
    if (!guard(acc, "delete", "삭제")) return;
    if (!redraft && !window.confirm("이 게시물을 삭제할까요?")) return;
    const t = theme();
    t.posts = t.posts.filter((p) => p.id !== post.id);
    const req = acc.proto === "ap"
      ? apCall(acc, "DELETE", `/api/v1/statuses/${post.remoteId || "{id}"}`)
      : atCall(acc, "com.atproto.repo.deleteRecord", { repo: acc.did, collection: "app.bsky.feed.post", rkey: String(post.uri || "").split("/").pop() || "{rkey}" });
    announce(redraft ? "↺ 삭제 후 다시 쓰기" : "🗑 삭제", req);
    if (cs.selectedPostId === post.id) cs.selectedPostId = null;
    if (redraft) openCompose({ redraft: post });
    api.renderAll();
  }

  function runMenu(cmd, post) {
    const acc = account(post.accountId);
    if (cmd === "copy") {
      try { navigator.clipboard.writeText(post.url); } catch (_) {}
      ticker(`🔗 링크 복사: ${post.url}`);
    } else if (cmd === "open") {
      window.open(post.url, "_blank", "noopener");
    } else if (cmd === "translate") {
      toggleTranslate(post);
    } else if (cmd === "mute-conv") {
      if (!guard(acc, "muteConv", "대화 뮤트")) return;
      post.convMuted = !post.convMuted;
      announce(post.convMuted ? "🔕 대화 뮤트" : "🔔 대화 뮤트 해제", acc.proto === "ap"
        ? apCall(acc, "POST", `/api/v1/statuses/${post.remoteId}/${post.convMuted ? "mute" : "unmute"}`)
        : atCall(acc, post.convMuted ? "app.bsky.graph.muteThread" : "app.bsky.graph.unmuteThread", { root: post.uri }));
    } else if (cmd === "pin") {
      if (!guard(acc, "pin", "프로필 고정")) return;
      post.pinned = !post.pinned;
      announce(post.pinned ? "📌 프로필에 고정" : "📌 고정 해제", apCall(acc, "POST", `/api/v1/statuses/${post.remoteId}/${post.pinned ? "pin" : "unpin"}`));
    } else if (cmd === "edit") {
      openCompose({ edit: post });
    } else if (cmd === "delete") {
      deletePost(post, false);
    } else if (cmd === "redraft") {
      deletePost(post, true);
    } else if (cmd === "profile") {
      openProfile(post.handle);
    } else if (cmd === "mention") {
      openCompose({ mention: post.handle, accountId: post.accountId });
    } else if (cmd === "list") {
      openProfile(post.handle);
      cs.listPicker = true;
      api.renderAll();
    } else if (cmd === "mute") {
      setMute(post.handle, !(acc.mutes || []).some((m) => m.handle === post.handle));
    } else if (cmd === "block") {
      setBlock(post.handle, !(acc.blocks || []).some((b) => b.handle === post.handle));
    } else if (cmd === "domain-block") {
      blockDomain(post.handle);
    } else if (cmd === "report") {
      openReport(post.handle, post);
    } else if (cmd === "filter") {
      addFilterFromPost(post);
    } else if (cmd === "send") {
      sendToKb(post, false);
    } else if (cmd === "ingest") {
      sendToKb(post, true);
    }
    api.renderAll();
  }

  function openMoreMenu(post) {
    const pop = document.getElementById("pop-post-actions");
    if (!pop || !post) return;
    cs.menuPostId = post.id;
    const acc = account(post.accountId);
    const ap = acc && acc.proto === "ap";
    const items = [];
    const item = (cmd, label, opts) => {
      const o = opts || {};
      items.push(`<button type="button" class="tt-pop-item" data-cl-menu="${cmd}" ${o.disabled ? "disabled" : ""} ${o.title ? `title="${esc(o.title)}"` : ""}><span>${label}</span>${o.kbd ? `<kbd>${o.kbd}</kbd>` : ""}</button>`);
    };
    const sep = (t) => items.push(`<div class="tt-pop-sep"></div>${t ? `<div class="tt-pop-sec">${esc(t)}</div>` : ""}`);
    item("copy", "🔗 링크 복사");
    item("open", "↗ 원격 서버에서 열기");
    if (ap && post.translation) item("translate", cs.translated.has(post.id) ? "🌐 원문 보기" : "🌐 번역", { disabled: !supports(acc, "translate"), title: supports(acc, "translate") ? "POST /api/v1/statuses/:id/translate" : "연결 서버가 번역을 제공하지 않음" });
    item("mute-conv", post.convMuted ? "🔔 대화 뮤트 해제" : "🔕 이 대화 뮤트", { disabled: !canWrite(acc, "muteConv") });
    if (post.own) {
      sep("내 게시물");
      if (ap) item("pin", post.pinned ? "📌 프로필 고정 해제" : "📌 프로필에 고정", { disabled: !canWrite(acc, "pin") });
      if (ap) item("edit", "✏️ 수정", { disabled: !canWrite(acc, "edit"), title: "PUT /api/v1/statuses/:id" });
      item("delete", "🗑 삭제", { disabled: !canWrite(acc, "delete") });
      item("redraft", "↺ 삭제 후 다시 쓰기", { disabled: !canWrite(acc, "delete") });
    } else if (post.handle) {
      const muted = (acc.mutes || []).some((m) => m.handle === post.handle);
      const blocked = (acc.blocks || []).some((b) => b.handle === post.handle);
      const domain = domainOf(post.handle);
      sep(post.handle);
      item("profile", "👤 프로필 보기");
      item("mention", "@ 멘션하기", { disabled: !canWrite(acc, "post") });
      if (ap) item("list", "📋 리스트에 추가 · 제거", { disabled: !canWrite(acc, "list") });
      item("mute", muted ? "🔊 뮤트 해제" : "🔇 뮤트", { disabled: !canWrite(acc, "mute") });
      item("block", blocked ? "차단 해제" : "⛔ 차단", { disabled: !canWrite(acc, "block") });
      if (ap && domain && domain !== acc.server) item("domain-block", `🌐 ${esc(domain)} 도메인 차단`, { disabled: !canWrite(acc, "block") });
      item("report", "🚩 신고…", { disabled: !canWrite(acc, "report") });
      if (ap) item("filter", "🛡️ 이 게시물로 필터 추가…", { disabled: !canWrite(acc, "filter") });
    }
    sep("지식베이스");
    item("send", "📥 수집함 보내기", { kbd: "s" });
    item("ingest", "⚡ 보내고 적재", { kbd: "i" });
    pop.innerHTML = items.join("");
  }

  /* ── 신고 ── */
  function openReport(handle, post) {
    const acc = profileAccount(handle);
    if (!guard(acc, "report", "신고")) return;
    const modal = document.getElementById("modal-report");
    const box = document.getElementById("modal-report-body");
    const RULES = [{ id: "1", text: "스팸 · 광고 금지" }, { id: "2", text: "혐오 표현 · 괴롭힘 금지" }, { id: "3", text: "민감한 미디어는 열람 주의(CW)로 가리기" }];
    const CATS = [["spam", "스팸"], ["legal", "법률 위반"], ["violation", "서버 규칙 위반"], ["other", "기타"]];
    const authored = posts().filter((p) => p.handle === handle && !p.notifOnly && p.accountId === acc.id);
    const st = { category: "spam", rules: new Set(), statuses: new Set(post ? [post.id] : []), comment: "", forward: true };
    const domain = domainOf(handle);
    const remote = acc.proto === "ap" && domain && domain !== acc.server;

    function request() {
      if (acc.proto === "ap") {
        const body = { account_id: "{account_id}", status_ids: [...st.statuses].map((id) => (byId(id) || {}).remoteId).filter(Boolean), category: st.category, comment: st.comment, forward: remote ? st.forward : false };
        if (st.category === "violation") body.rule_ids = [...st.rules];
        return apCall(acc, "POST", "/api/v1/reports", body);
      }
      const p = byId([...st.statuses][0]);
      const reasonType = { spam: "reasonSpam", violation: "reasonViolation", legal: "reasonViolation", other: "reasonOther" }[st.category];
      return atCall(acc, "com.atproto.moderation.createReport", { reasonType: `com.atproto.moderation.defs#${reasonType}`, subject: p ? { $type: "com.atproto.repo.strongRef", uri: p.uri, cid: p.cid || "{cid}" } : { $type: "com.atproto.admin.defs#repoRef", did: "{did}" }, reason: st.comment });
    }

    function refresh() {
      const req = request();
      box.querySelector("#report-preview").textContent = `${req.method} ${req.url}\n${JSON.stringify(req.body, null, 2)}`;
    }

    function draw() {
      box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
          <div><h2 id="modal-report-title" style="margin:0;font-size:16px;font-weight:700">🚩 ${esc(handle)} 신고</h2>
          <p style="margin:4px 0 0;font-size:12px;color:var(--muted)">신고는 연결 서버(${esc(acc.server)}) 운영진에게 전달됩니다.</p></div>
          <button type="button" data-close-dialog="modal-report" class="tt-btn">✕</button>
        </div>
        <fieldset class="tt-fieldset"><legend>신고 사유</legend>
          <div class="tt-acc-pick-row">${CATS.map(([v, l]) => `<label class="tt-acc-pick ${st.category === v ? "on" : ""}"><input type="radio" name="report-cat" value="${v}" ${st.category === v ? "checked" : ""}/> ${l}</label>`).join("")}</div>
          ${st.category === "violation" && acc.proto === "ap" ? `<div class="cl-report-rules">${RULES.map((r) => `<label><input type="checkbox" data-report-rule="${r.id}" ${st.rules.has(r.id) ? "checked" : ""}/> ${r.id}. ${esc(r.text)}</label>`).join("")}</div>` : ""}
        </fieldset>
        ${authored.length ? `<fieldset class="tt-fieldset"><legend>함께 첨부할 게시물</legend>${authored.map((p) => `<label class="cl-report-post"><input type="checkbox" data-report-status="${esc(p.id)}" ${st.statuses.has(p.id) ? "checked" : ""}/> ${esc(p.text.slice(0, 80))}</label>`).join("")}</fieldset>` : ""}
        <label class="cl-note"><span>추가 설명 (선택)</span><textarea id="report-comment" rows="2">${esc(st.comment)}</textarea></label>
        ${remote ? `<label class="cl-switch-row"><input type="checkbox" id="report-forward" ${st.forward ? "checked" : ""}/> 원격 서버(${esc(domain)})에도 익명으로 사본 전달 (forward)</label>` : ""}
        <pre id="report-preview" class="cl-preview"></pre>
        <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:10px;border-top:1px solid var(--border)">
          <button type="button" data-close-dialog="modal-report" class="tt-btn">취소</button>
          <button type="button" id="report-submit" class="tt-btn tt-btn-pri">신고 보내기</button>
        </div>`;
      box.querySelectorAll('input[name="report-cat"]').forEach((r) => r.addEventListener("change", () => { st.category = r.value; draw(); }));
      box.querySelectorAll("[data-report-rule]").forEach((c) => c.addEventListener("change", () => { toggleKey(st.rules, c.getAttribute("data-report-rule")); refresh(); }));
      box.querySelectorAll("[data-report-status]").forEach((c) => c.addEventListener("change", () => { toggleKey(st.statuses, c.getAttribute("data-report-status")); refresh(); }));
      box.querySelector("#report-comment").addEventListener("input", (e) => { st.comment = e.target.value; refresh(); });
      const fwd = box.querySelector("#report-forward");
      if (fwd) fwd.addEventListener("change", () => { st.forward = fwd.checked; refresh(); });
      box.querySelectorAll("[data-close-dialog]").forEach((b) => b.addEventListener("click", () => modal.close()));
      box.querySelector("#report-submit").addEventListener("click", () => {
        const req = request();
        modal.close();
        announce(`🚩 ${handle} 신고 접수`, req);
      });
      refresh();
    }

    draw();
    modal.showModal();
  }

  /* ── 지식베이스 브리지 ── */
  function ensureClientFeed(acc) {
    const id = `fc-${acc.id}`;
    let feed = theme().feeds.find((f) => f.id === id);
    if (!feed) {
      feed = { id, folder: "연합 클라이언트", proto: acc.proto, format: acc.proto === "ap" ? "ActivityPub" : "AT Protocol", interval: "수동", title: `수동 보내기 (${acc.handle})`, handle: acc.handle, url: `https://${acc.server}`, via: acc.id, source: "연합 클라이언트 탭에서 수집함 보내기", etag: "연결 계정 경유 · 수동" };
      theme().feeds.push(feed);
    }
    return feed;
  }

  function createKbItemFromPost(post, feedId) {
    const acc = account(post.accountId);
    const first = post.text.split("\n")[0];
    const firstLine = post.card ? post.card.title : (first.length > 90 ? `${first.slice(0, 89)}…` : first);
    const mediaHtmlKb = (post.media || []).length ? `<ul>${post.media.map((m) => `<li>🖼 ${esc(m.label || "image")} — 대체 텍스트: ${esc(m.alt || "(없음)")}</li>`).join("")}</ul>` : "";
    const pollHtmlKb = post.poll ? `<ul>${post.poll.options.map((o) => `<li>📊 ${esc(o.title)} — ${o.votes}표</li>`).join("")}</ul>` : "";
    const item = {
      id: `kb_${post.id}_${Date.now().toString(36)}`, feedId, proto: acc.proto, format: acc.proto === "ap" ? "AP" : "AT-URI", viaAccount: acc.id,
      title: post.cw ? `⚠ ${post.cw}` : firstLine, author: post.handle, url: post.url,
      apObjectUri: acc.proto === "ap" ? post.uri : undefined, remoteId: acc.proto === "ap" ? post.remoteId : undefined,
      atUri: acc.proto === "at" ? post.uri : undefined, atCid: acc.proto === "at" ? post.cid : undefined,
      timeAgo: post.time, timestamp: post.timestamp, unread: true, starred: false, score: affinityScore(post),
      sourceTags: [...(post.hashtags || [])],
      rawBody: `${post.cw ? `[CW: ${post.cw}] ` : ""}${post.text}${(post.media || []).map((m) => ` [이미지: ${m.alt || m.label}]`).join("")}`,
      rawHtml: `${post.cw ? `<p><strong>열람 주의:</strong> ${esc(post.cw)}</p>` : ""}<p>${textHtml(post.text)}</p>${mediaHtmlKb}${pollHtmlKb}${post.card ? `<blockquote><strong>${esc(post.card.title)}</strong><br/>${esc(post.card.description || "")}<br/><a href="${esc(post.card.url)}" target="_blank" rel="noopener noreferrer">${esc(post.card.url)}</a></blockquote>` : ""}<p style="font-size:0.85em;color:var(--muted)">연결 계정 <code>${esc(acc.handle)}</code> 경유 수집 · <code>${esc(post.uri || post.url)}</code></p>`,
      ingestState: "unlinked", linkedDoc: null, publishState: "none", publishedInfo: null
    };
    theme().items.unshift(item);
    post.kbItemId = item.id;
    return item;
  }

  function sendToKb(post, ingestNow) {
    if (!post || post.notifOnly || post.scheduled) return;
    const rel = kbItemFor(post);
    if (rel) {
      if (ingestNow && rel.item.ingestState !== "linked") {
        api.ingestItemToClaire(rel.item.id, "");
        return;
      }
      ticker(rel.via === "url"
        ? `동일 URL이 이미 지식베이스에 있습니다 (${rel.item.id}) — canonical_url 기준 중복 수집 생략`
        : `이미 지식베이스 수집함에 있습니다 (${rel.item.id})`);
      api.renderAll();
      return;
    }
    const acc = account(post.accountId);
    const item = createKbItemFromPost(post, ensureClientFeed(acc).id);
    if (ingestNow) {
      api.ingestItemToClaire(item.id, "");
    } else {
      ticker(`📥 지식베이스 수집함으로 보냄 (${item.id}) — 해시태그 ${item.sourceTags.length}개는 #수집 태그로 보존되며 엔티티는 적재 시 생성됩니다.`);
      api.renderAll();
    }
  }

  function sendLinkToKb(acc, link) {
    const t = theme();
    if (t.items.some((i) => i.url === link.url)) {
      ticker(`동일 URL이 이미 지식베이스에 있습니다 — canonical_url 기준 중복 수집 생략 (${link.url})`);
      return;
    }
    const fid = `fc-trends-${acc.id}`;
    if (!t.feeds.some((f) => f.id === fid)) {
      t.feeds.push({ id: fid, folder: "연합 클라이언트", proto: "rss", format: "WEB", interval: "수동", title: `탐색 · 트렌드 링크 (${acc.server})`, url: `https://${acc.server}/api/v1/trends/links`, etag: "트렌드 링크에서 수동 수집" });
    }
    t.items.unshift({
      id: `kb_link_${Date.now().toString(36)}`, feedId: fid, proto: "rss", format: "WEB",
      title: link.title, author: link.author || link.provider, url: link.url,
      timeAgo: "방금", timestamp: Math.floor(Date.now() / 1000), unread: true, starred: false, score: 60,
      sourceTags: [], rawBody: link.description, rawHtml: `<p>${esc(link.description)}</p><p style="font-size:0.85em;color:var(--muted)">${esc(acc.server)} 트렌드 링크 · ${link.shares}명 공유</p>`,
      ingestState: "unlinked", linkedDoc: null, publishState: "none", publishedInfo: null
    });
    ticker(`📥 트렌드 링크를 지식베이스 수집함으로 보냄 — ${link.title}`);
    api.renderAll();
  }

  function registerFeed(acc, feed, importPosts) {
    if (theme().feeds.some((f) => f.id === feed.id)) {
      ticker(`이미 지식베이스 수집 대상입니다 — ${feed.title}`);
      return;
    }
    api.addKbFeed(feed);
    let imported = 0;
    importPosts.forEach((p) => {
      if (kbItemFor(p)) return;
      createKbItemFromPost(p, feed.id);
      imported += 1;
    });
    ticker(`⇢ '${feed.title}' 를 지식베이스 정기 수집 대상으로 등록 (경유: ${acc.handle}) — 첫 수집 ${imported}건, 엔티티는 적재 시 생성됩니다.`);
    api.renderAll();
  }

  function registerTimeline(acc, tl) {
    registerFeed(acc, {
      id: `f_tl_${acc.id}_${tl.id.replace(/[^\w-]/g, "_")}`, folder: "연합 타임라인", proto: acc.proto,
      format: acc.proto === "ap" ? "ActivityPub" : "AT Protocol", interval: acc.proto === "ap" ? "stream" : "15m",
      title: `${tl.label} (${acc.handle})`, handle: acc.handle, url: timelineCall(acc, tl), via: acc.id, timelineId: tl.id,
      source: `${tl.label} · ${tl.api}`, etag: "연결 계정 경유 · 등록됨"
    }, visiblePosts().filter((p) => !p.notifOnly && !p.scheduled));
  }

  function registerAuthor(handle) {
    const acc = profileAccount(handle);
    if (!acc) return;
    const pr = profileOf(handle);
    registerFeed(acc, {
      id: `f_acct_${acc.id}_${handle.replace(/[^\w-]/g, "_")}`, folder: "연합 계정", proto: acc.proto,
      format: acc.proto === "ap" ? "ActivityPub" : "AT Protocol", interval: acc.proto === "ap" ? "stream" : "15m",
      title: `${pr.name} (${handle})`, handle, url: handle, via: acc.id,
      source: acc.proto === "ap" ? "계정 게시물 · GET /api/v1/accounts/:id/statuses" : "작성자 피드 · app.bsky.feed.getAuthorFeed",
      etag: "연결 계정 경유 · 등록됨"
    }, posts().filter((p) => p.handle === handle && !p.notifOnly && !p.scheduled && p.accountId === acc.id));
  }

  /* ── 프로필 관계 동작 ── */
  function runRel(key, handle) {
    const acc = profileAccount(handle);
    const pr = profileOf(handle);
    const rel = pr.rel;
    if (key === "follow") {
      if (!guard(acc, "follow", "팔로우")) return;
      rel.following = !rel.following;
      if (typeof pr.followers === "number") pr.followers += rel.following ? 1 : -1;
      if (!rel.following) { rel.notify = false; rel.lists = []; }
      announce(rel.following ? `👤 ${handle} 팔로우` : `${handle} 언팔로우`, acc.proto === "ap"
        ? apCall(acc, "POST", `/api/v1/accounts/{account_id}/${rel.following ? "follow" : "unfollow"}`, rel.following ? { reblogs: rel.showReblogs, notify: rel.notify } : undefined)
        : atCall(acc, rel.following ? "com.atproto.repo.createRecord" : "com.atproto.repo.deleteRecord", rel.following ? { repo: acc.did, collection: "app.bsky.graph.follow", record: { $type: "app.bsky.graph.follow", subject: "{did}", createdAt: new Date().toISOString() } } : { repo: acc.did, collection: "app.bsky.graph.follow", rkey: "{rkey}" }));
    } else if (key === "notify" || key === "reblogs") {
      if (!guard(acc, "follow", "팔로우 설정")) return;
      if (!rel.following) { ticker("팔로우 중인 계정에만 설정할 수 있습니다."); return; }
      if (key === "notify") rel.notify = !rel.notify; else rel.showReblogs = !rel.showReblogs;
      announce(key === "notify" ? (rel.notify ? "🔔 새 글 알림 켜기" : "🔕 새 글 알림 끄기") : (rel.showReblogs ? "🔁 부스트 표시" : "🔁 부스트 숨기기"),
        apCall(acc, "POST", "/api/v1/accounts/{account_id}/follow", { reblogs: rel.showReblogs, notify: rel.notify }));
    } else if (key === "lists") {
      if (!guard(acc, "list", "리스트")) return;
      cs.listPicker = !cs.listPicker;
    } else if (key === "note") {
      cs.noteEdit = !cs.noteEdit;
    } else if (key === "mute") {
      setMute(handle, !(acc.mutes || []).some((m) => m.handle === handle));
      return;
    } else if (key === "block") {
      setBlock(handle, !(acc.blocks || []).some((b) => b.handle === handle));
      return;
    } else if (key === "report") {
      openReport(handle, null);
      return;
    } else if (key === "register") {
      registerAuthor(handle);
      return;
    } else if (key === "edit-profile") {
      announce("프로필 편집", apCall(acc, "PATCH", "/api/v1/accounts/update_credentials", { display_name: acc.displayName, note: "…", fields_attributes: [] }));
      return;
    }
    api.renderAll();
  }

  function toggleListMember(handle, listId) {
    const acc = profileAccount(handle);
    const rel = profileOf(handle).rel;
    if (!guard(acc, "list", "리스트")) return;
    if (!rel.following) { ticker("Mastodon 리스트에는 팔로우 중인 계정만 추가할 수 있습니다."); return; }
    const has = rel.lists.includes(listId);
    rel.lists = has ? rel.lists.filter((l) => l !== listId) : [...rel.lists, listId];
    announce(has ? "📋 리스트에서 제거" : "📋 리스트에 추가", apCall(acc, has ? "DELETE" : "POST", `/api/v1/lists/${listId}/accounts`, { account_ids: ["{account_id}"] }));
    api.renderAll();
  }

  /* ── 관리 화면 동작 ── */
  function runManage(cmd, arg) {
    const acc = account(cs.accountId);
    if (!acc) return;
    if (cmd === "filter-del") {
      if (!guard(acc, "filter", "필터")) return;
      acc.filters = acc.filters.filter((f) => f.id !== arg);
      announce("🛡️ 필터 삭제", apCall(acc, "DELETE", `/api/v2/filters/${arg}`));
    } else if (cmd === "unmute") {
      setMute(arg, false);
      return;
    } else if (cmd === "unblock") {
      setBlock(arg, false);
      return;
    } else if (cmd === "undomain") {
      if (!guard(acc, "block", "도메인 차단")) return;
      acc.domainBlocks = acc.domainBlocks.filter((d) => d !== arg);
      announce(`🌐 ${arg} 도메인 차단 해제`, apCall(acc, "DELETE", "/api/v1/domain_blocks", { domain: arg }));
    } else if (cmd === "untag") {
      if (!guard(acc, "follow", "해시태그 팔로우")) return;
      acc.followedTags = acc.followedTags.filter((t) => t !== arg);
      acc.timelines = acc.timelines.filter((t) => !(t.dynamic && t.id === `tag:${arg}`));
      announce(`# ${arg} 언팔로우`, apCall(acc, "POST", `/api/v1/tags/${arg}/unfollow`));
    } else if (cmd === "list-del") {
      if (!guard(acc, "list", "리스트")) return;
      acc.lists = acc.lists.filter((l) => l.id !== arg);
      announce("📋 리스트 삭제", apCall(acc, "DELETE", `/api/v1/lists/${arg}`));
    } else if (cmd === "list-new") {
      if (!guard(acc, "list", "리스트")) return;
      const title = window.prompt("새 리스트 이름:", "");
      if (!title || !title.trim()) return;
      acc.lists = acc.lists || [];
      acc.lists.push({ id: `l-${Date.now().toString(36)}`, title: title.trim() });
      announce("📋 리스트 생성", apCall(acc, "POST", "/api/v1/lists", { title: title.trim(), replies_policy: "list", exclusive: false }));
    }
    api.renderAll();
  }

  function followTag(acc, name) {
    if (!guard(acc, "follow", "해시태그 팔로우")) return;
    acc.followedTags = acc.followedTags || [];
    const followed = acc.followedTags.includes(name);
    if (followed) {
      acc.followedTags = acc.followedTags.filter((t) => t !== name);
    } else {
      acc.followedTags.push(name);
      ensureTagTimeline(acc, name);
    }
    announce(followed ? `# ${name} 언팔로우` : `# ${name} 팔로우 (홈 타임라인에 포함)`, apCall(acc, "POST", `/api/v1/tags/${name}/${followed ? "unfollow" : "follow"}`));
    api.renderAll();
  }

  function openTag(name) {
    const acc = account(cs.accountId) && account(cs.accountId).proto === "ap" ? account(cs.accountId) : apAccounts()[0];
    if (!acc) { cs.serverQuery = `#${name}`; switchTimeline(null, "search"); api.renderAll(); return; }
    switchTimeline(acc.id, ensureTagTimeline(acc, name));
    api.renderAll();
  }

  function runDemo(ref) {
    const [gi, ii] = ref.split(":").map(Number);
    const it = FEATURES[gi] && FEATURES[gi].items[ii];
    const acc = apAccounts()[0];
    if (!it || !acc) return;
    const d = it.demo || {};
    if (it.feature && !supports(acc, it.feature)) {
      ticker(`⊘ '${it.label}' — 연결 서버(${acc.server})가 제공하지 않는 기능이라 비활성입니다.`);
      return;
    }
    if (d.timeline) {
      if (!acc.timelines.some((t) => t.id === d.timeline)) { ticker(`이 테마의 연결 계정에는 '${d.timeline}' 타임라인이 없습니다 — 기본 지식베이스 테마에서 시연됩니다.`); return; }
      switchTimeline(acc.id, d.timeline);
    } else if (d.post) {
      if (!byId(d.post)) { ticker("이 시연 게시물은 기본 지식베이스 테마에 있습니다."); return; }
      openPost(d.post);
    } else if (d.profile) {
      cs.accountId = acc.id;
      openProfile(d.profile);
      return;
    } else if (d.search) {
      cs.accountId = acc.id;
      cs.prevTimelineId = cs.timelineId;
      cs.serverQuery = d.search;
      switchTimeline(acc.id, "search");
    } else if (d.compose) {
      openCompose(d.compose === "quote" ? { quote: byId("p0-1") || posts().find((p) => p.accountId === acc.id && !p.notifOnly) } : {});
      return;
    }
    api.renderAll();
  }

  /* ── 패널 이벤트 위임 ── */
  function onPanelClick(e) {
    const t = e.target;
    const hit = (sel) => t.closest(sel);
    let x;
    if ((x = hit("[data-cl-reveal]"))) { toggleKey(cs.revealed, x.getAttribute("data-cl-reveal")); api.renderAll(); return; }
    if ((x = hit("[data-cl-alt]"))) { toggleKey(cs.altOpen, x.getAttribute("data-cl-alt")); api.renderAll(); return; }
    if ((x = hit("[data-cl-translate]"))) { const p = byId(x.getAttribute("data-cl-translate")); if (p) toggleTranslate(p); return; }
    if ((x = hit("[data-cl-history]"))) {
      const p = byId(x.getAttribute("data-cl-history"));
      toggleKey(cs.historyOpen, p.id);
      if (cs.historyOpen.has(p.id)) announce("✏️ 수정 이력", apCall(account(p.accountId), "GET", `/api/v1/statuses/${p.remoteId}/history`));
      api.renderAll();
      return;
    }
    if ((x = hit("[data-cl-vote]"))) { const p = byId(x.getAttribute("data-cl-vote")); if (p) votePoll(p, x); return; }
    if ((x = hit("[data-cl-profile]"))) { e.stopPropagation(); openProfile(x.getAttribute("data-cl-profile")); return; }
    if ((x = hit("[data-cl-resolve]"))) {
      const h = x.getAttribute("data-cl-resolve");
      profileOf(h);
      openProfile(h);
      return;
    }
    if ((x = hit("[data-cl-more]"))) { openMoreMenu(byId(x.getAttribute("data-cl-more"))); return; }
    if ((x = hit("[data-cl-open]"))) { selectPost(x.getAttribute("data-cl-open")); return; }
    if ((x = hit("[data-cl-act]"))) {
      e.stopPropagation();
      const post = byId(x.getAttribute("data-cl-id"));
      if (post) runAction(x.getAttribute("data-cl-act"), post);
      return;
    }
    if ((x = hit("[data-cl-tl-id]"))) {
      switchTimeline(x.getAttribute("data-cl-tl") || cs.accountId, x.getAttribute("data-cl-tl-id"));
      api.renderAll();
      return;
    }
    if (hit("[data-cl-connect]")) { openConnect(); return; }
    if (hit("[data-cl-refresh]")) {
      const acc = account(cs.accountId);
      const tl = currentTimeline();
      const latest = visiblePosts()[0];
      const cursor = acc.proto === "ap" ? `?since_id=${latest && latest.remoteId ? latest.remoteId : ""}` : "?limit=50&cursor=…";
      ticker(`↻ ${timelineCall(acc, tl)}${cursor} → 새 게시물 0건 (프로토타입 시뮬레이션 · 서버 레이트 리밋 헤더 준수)`);
      return;
    }
    if (hit("[data-cl-register]")) { registerTimeline(account(cs.accountId), currentTimeline()); return; }
    if ((x = hit("[data-cl-notif-filter]"))) { cs.notifFilter = x.getAttribute("data-cl-notif-filter"); cs.selectedPostId = null; api.renderAll(); return; }
    if (hit("[data-cl-requests]")) { cs.showRequests = !cs.showRequests; api.renderAll(); return; }
    if ((x = hit("[data-cl-req]"))) { runNotifRequest(x.getAttribute("data-cl-req"), x.getAttribute("data-cl-arg")); return; }
    if ((x = hit("[data-cl-explore]"))) { cs.exploreTab = x.getAttribute("data-cl-explore"); cs.selectedPostId = null; api.renderAll(); return; }
    if ((x = hit("[data-cl-tag-follow]"))) { followTag(account(cs.accountId), x.getAttribute("data-cl-tag-follow")); return; }
    if ((x = hit("[data-cl-tag-open]"))) { openTag(x.getAttribute("data-cl-tag-open")); return; }
    if ((x = hit("[data-cl-link-kb]"))) {
      const acc = account(cs.accountId);
      sendLinkToKb(acc, acc.trends.links[Number(x.getAttribute("data-cl-link-kb"))]);
      return;
    }
    if ((x = hit("[data-cl-mg]"))) { runManage(x.getAttribute("data-cl-mg"), x.getAttribute("data-cl-arg")); return; }
    if ((x = hit("[data-cl-rel]"))) { runRel(x.getAttribute("data-cl-rel"), x.getAttribute("data-cl-handle")); return; }
    if ((x = hit("[data-cl-list-toggle]"))) { toggleListMember(x.getAttribute("data-cl-handle"), x.getAttribute("data-cl-list-toggle")); return; }
    if ((x = hit("[data-cl-demo]"))) { runDemo(x.getAttribute("data-cl-demo")); return; }
    if ((x = hit("[data-cl-search-tag]"))) {
      cs.search = `#${x.getAttribute("data-cl-search-tag")}`;
      const input = document.getElementById("client-search");
      if (input) input.value = cs.search;
      api.renderAll();
      return;
    }
    if (hit("[data-cl-search-close]")) { closeSearch(); return; }
    if (hit("[data-cl-back]")) { cs.profile = null; api.renderAll(); return; }
    if ((x = hit("[data-cl-post]")) && !hit("button, a, input, label, select, textarea")) selectPost(x.getAttribute("data-cl-post"));
  }

  function onPanelChange(e) {
    const t = e.target;
    const acc = account(cs.accountId);
    if (t.matches("[data-cl-policy]") && acc) {
      if (!guard(acc, "notifications", "알림 정책")) return;
      acc.notifPolicy[t.getAttribute("data-cl-policy")] = t.value;
      const key = t.getAttribute("data-cl-policy").replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
      announce("🔔 알림 정책 변경", apCall(acc, "PATCH", "/api/v2/notifications/policy", { [key]: t.value }));
    } else if (t.matches("[data-cl-note]")) {
      const handle = t.getAttribute("data-cl-note");
      const pacc = profileAccount(handle);
      if (!guard(pacc, "note", "개인 메모")) return;
      profileOf(handle).rel.note = t.value;
      announce("📝 개인 메모 저장", apCall(pacc, "POST", "/api/v1/accounts/{account_id}/note", { comment: t.value }));
    }
  }

  function onPanelSubmit(e) {
    const form = e.target.closest("[data-cl-filter-form]");
    if (!form) return;
    e.preventDefault();
    const acc = account(cs.accountId);
    if (!guard(acc, "filter", "필터")) return;
    const fd = new FormData(form);
    const ctx = fd.getAll("ctx");
    addFilter(acc, String(fd.get("keyword") || ""), String(fd.get("action") || "warn"), ctx.length ? ctx : ["home"]);
  }

  function runNotifRequest(cmd, id) {
    const acc = account(cs.accountId);
    if (!guard(acc, "notifications", "알림 요청")) return;
    const r = (acc.notifRequests || []).find((x) => x.id === id);
    if (!r) return;
    acc.notifRequests = acc.notifRequests.filter((x) => x.id !== id);
    if (cmd === "accept") {
      posts().push({
        id: `nr_${id}`, accountId: acc.id, timelines: ["notifications"], unread: true, visibility: "public", lang: "ko",
        notif: { kind: "mention", by: r.handle }, author: r.handle.split("@")[1], handle: r.handle, time: "방금", timestamp: Math.floor(Date.now() / 1000),
        text: r.preview, hashtags: [], card: null, url: `https://${domainOf(r.handle)}`, uri: "", remoteId: "",
        counts: { replies: 0, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false
      });
    }
    announce(cmd === "accept" ? `📨 ${r.handle} 알림 요청 수락` : `📨 ${r.handle} 알림 요청 거절`, apCall(acc, "POST", `/api/v1/notifications/requests/${id}/${cmd}`));
    api.renderAll();
  }

  function runAction(act, post) {
    if (act === "reply") openCompose({ replyTo: post });
    else if (act === "quote") openCompose({ quote: post });
    else if (act === "boost") toggleAction(post, "boost");
    else if (act === "fav") toggleAction(post, "favourite");
    else if (act === "bookmark") toggleAction(post, "bookmark");
    else if (act === "send") sendToKb(post, false);
    else if (act === "ingest") sendToKb(post, true);
    else if (act === "open-kb") {
      const rel = kbItemFor(post);
      if (rel) api.openKbItem(rel.item.id);
    } else if (act === "sched-cancel" || act === "sched-edit") {
      const acc = account(post.accountId);
      if (!guard(acc, "post", "예약 게시물")) return;
      if (act === "sched-cancel") {
        theme().posts = posts().filter((p) => p.id !== post.id);
        announce("🗓️ 예약 취소", apCall(acc, "DELETE", `/api/v1/scheduled_statuses/${post.scheduled.id}`));
      } else {
        const at = window.prompt("새 예약 시각 (예: 11/03 (월) 10:00):", post.scheduled.at);
        if (!at) return;
        post.scheduled.at = at;
        announce("🗓️ 예약 시각 변경", apCall(acc, "PUT", `/api/v1/scheduled_statuses/${post.scheduled.id}`, { scheduled_at: "{ISO 8601}" }));
      }
      api.renderAll();
    }
  }

  function closeSearch() {
    cs.serverQuery = "";
    cs.timelineId = cs.prevTimelineId;
    cs.selectedPostId = null;
    const input = document.getElementById("client-search");
    if (input) input.value = "";
    cs.search = "";
    api.renderAll();
  }

  /* ── 새 글 · 답글 · 인용 · 수정 · 삭제 후 다시 쓰기 (Mastodon 5.0 기준 작성 기능) ── */
  function openCompose(ctx) {
    ctx = ctx || {};
    const pubAccs = api.publishAccounts();
    if (!pubAccs.length) {
      ticker("🔒 이 테마에는 글을 쓸 수 있는 연결 계정이 없습니다 (읽기 전용 scope).");
      return;
    }
    const editOf = ctx.edit || null;
    const replyTo = ctx.replyTo || null;
    const quote = ctx.quote || null;
    const src = editOf || ctx.redraft || null;
    let selected;
    if (editOf || replyTo) {
      const a = account((editOf || replyTo).accountId);
      if (!canWrite(a, editOf ? "edit" : "reply")) { ticker(`🔒 ${a ? a.handle : "계정"} 은(는) 읽기 전용이라 ${editOf ? "수정" : "답글"}할 수 없습니다.`); return; }
      selected = new Set([a.id]);
    } else if (ctx.redraft) {
      selected = new Set([ctx.redraft.accountId]);
    } else if (ctx.accountId && pubAccs.some((a) => a.id === ctx.accountId)) {
      selected = new Set([ctx.accountId]);
    } else if (quote) {
      const same = pubAccs.find((a) => a.proto === postProto(quote));
      selected = new Set([(same || pubAccs[0]).id]);
    } else {
      selected = new Set(pubAccs.map((a) => a.id));
    }
    const replyVis = replyTo ? (["direct", "private"].includes(replyTo.visibility) ? replyTo.visibility : (replyTo.visibility === "unlisted" ? "unlisted" : "public")) : "public";
    const st = {
      text: src ? src.text : (replyTo && postProto(replyTo) === "ap" ? `${replyTo.handle} ` : (ctx.mention ? `${ctx.mention} ` : "")),
      cwOn: !!((src && src.cw) || (replyTo && replyTo.cw)),
      cw: src ? (src.cw || "") : (replyTo && replyTo.cw ? replyTo.cw : ""),
      visibility: src ? (src.visibility || "public") : replyVis,
      language: src ? (src.lang || "ko") : "ko",
      media: src && src.media ? src.media.map((m) => ({ label: m.label, alt: m.alt })) : [],
      sensitive: !!(src && src.sensitive),
      poll: src && src.poll ? { options: src.poll.options.map((o) => o.title), expiresIn: 86400, multiple: !!src.poll.multiple, hideTotals: false } : null,
      scheduleOn: false,
      scheduledAt: "",
      quotePolicy: src && src.quotePolicy ? src.quotePolicy : "public"
    };
    const modal = document.getElementById("modal-compose");
    const box = document.getElementById("modal-compose-body");
    const EXPIRES = [[300, "5분"], [3600, "1시간"], [86400, "1일"], [259200, "3일"], [604800, "7일"]];

    function chosen() { return pubAccs.filter((a) => selected.has(a.id) && !(st.scheduleOn && a.proto !== "ap")); }
    function hasAp() { return pubAccs.some((a) => selected.has(a.id) && a.proto === "ap"); }
    function tagsOf(t) { return [...t.matchAll(/#([\p{L}\p{N}_]+)/gu)].map((m) => m[1]); }
    function rootOf(p) { let cur = p; while (cur && cur.replyTo) { const up = byId(cur.replyTo); if (!up) break; cur = up; } return cur; }
    function pollValid() { return !st.poll || (st.poll.options.length >= 2 && st.poll.options.every((o) => o.trim())); }

    function requests() {
      return chosen().map((acc) => ({
        acc,
        req: api.buildServerRequest(acc, {
          kind: "post", text: st.text, tags: tagsOf(st.text),
          cw: acc.proto === "ap" && st.cwOn ? st.cw : "", visibility: st.visibility, language: st.language,
          media: st.media, sensitive: st.sensitive, poll: acc.proto === "ap" ? st.poll : null,
          scheduledAt: acc.proto === "ap" && st.scheduleOn && st.scheduledAt ? new Date(st.scheduledAt).toISOString() : "",
          quotePolicy: acc.proto === "ap" ? st.quotePolicy : "",
          replyTo: replyTo ? postTarget(replyTo) : null,
          replyRoot: replyTo ? postTarget(rootOf(replyTo)) : null,
          quote: quote ? postTarget(quote) : null,
          editOf: editOf ? (editOf.remoteId || "{id}") : null
        })
      }));
    }

    function updateDynamic() {
      let over = false;
      box.querySelector("#compose-counters").innerHTML = chosen().map((a) => {
        const n = a.proto === "at" ? api.graphemeLength(st.text) : [...st.text].length + (st.cwOn ? [...st.cw].length : 0);
        const bad = n > a.charLimit;
        over = over || bad;
        return `<span class="cl-counter ${bad ? "over" : ""}">${protoDot(a.proto)} ${n}/${a.charLimit}${a.proto === "at" ? " graphemes" : "자"}</span>`;
      }).join("");
      box.querySelector("#compose-preview").textContent = requests().map((r) => `# ${r.acc.handle}\n${r.req.note ? `# ${r.req.note}\n` : ""}${r.req.method} ${r.req.url}\n${JSON.stringify(r.req.body, null, 2)}`).join("\n\n") || "(계정을 선택하세요)";
      const empty = !st.text.trim() && !st.media.length;
      box.querySelector("#compose-submit").disabled = empty || !chosen().length || over || !pollValid() || (st.scheduleOn && !st.scheduledAt);
    }

    function draw() {
      const ctxPost = replyTo || quote;
      const ap = hasAp();
      const atSelected = pubAccs.some((a) => selected.has(a.id) && a.proto === "at");
      const apAcc = pubAccs.find((a) => selected.has(a.id) && a.proto === "ap");
      const heading = editOf ? "✏️ 게시물 수정" : (ctx.redraft ? "↺ 삭제 후 다시 쓰기" : (replyTo ? "↩ 답글 작성" : (quote ? "❝ 인용 작성" : "✎ 새 글 작성")));
      const atNotes = [];
      if (atSelected && (st.poll || (st.cwOn && st.cw) || st.visibility !== "public")) atNotes.push("AT 계정에는 투표 · 열람 주의 · 공개 범위가 적용되지 않습니다");
      if (atSelected && st.scheduleOn) atNotes.push("예약 게시는 Mastodon 계정에만 적용되어 AT 계정은 이번 게시에서 제외됩니다");
      box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
          <div>
            <h2 id="modal-compose-title" style="margin:0;font-size:16px;font-weight:700">${heading}</h2>
            <p style="margin:4px 0 0;font-size:12px;color:var(--muted)">선택한 연결 계정의 서버 API로 게시합니다 (${editOf ? "PUT /api/v1/statuses/:id — 수정 이력이 남습니다" : (replyTo ? "답글은 원 게시물과 같은 계정으로만" : "여러 계정 선택 시 교차 게시")}).</p>
          </div>
          <button type="button" data-close-dialog="modal-compose" class="tt-btn">✕</button>
        </div>
        ${ctxPost ? `<div class="cl-compose-ctx"><div>${quote ? "인용 대상" : "답글 대상"} · <b>${esc(ctxPost.author)}</b> <span class="cl-handle">${esc(ctxPost.handle)}</span></div><div class="cl-post-text clamp-2">${textHtml(ctxPost.text)}</div>${quote && !chosen().every((a) => a.proto === postProto(quote) && (a.proto === "at" || supports(a, "quote"))) ? `<small>다른 프로토콜 계정이나 인용을 제공하지 않는 서버에는 원문 링크로 인용됩니다.</small>` : ""}</div>` : ""}
        <fieldset class="tt-fieldset">
          <legend>게시할 연결 계정</legend>
          <div class="tt-acc-pick-row">${pubAccs.map((a) => {
            const locked = !!(editOf || replyTo || ctx.redraft) && !selected.has(a.id);
            return `<label class="tt-acc-pick ${selected.has(a.id) ? "on" : ""} ${locked ? "locked" : ""}"><input type="checkbox" data-compose-acc="${esc(a.id)}" ${selected.has(a.id) ? "checked" : ""} ${editOf || replyTo || ctx.redraft ? "disabled" : ""}/><span>${protoDot(a.proto)} <b>${esc(a.handle)}</b> <small>${esc(a.software)}</small></span></label>`;
          }).join("")}</div>
        </fieldset>
        ${st.cwOn ? `<input id="compose-cw" type="text" class="tt-search cl-cw-input" placeholder="열람 주의 문구 (spoiler_text)" value="${esc(st.cw)}"/>` : ""}
        <div>
          <label for="compose-text" class="sr-only">본문</label>
          <textarea id="compose-text" class="cl-compose-text" rows="5" placeholder="무엇을 공유할까요? #해시태그는 AP Hashtag · AT facet 태그로 변환됩니다."></textarea>
          <div id="compose-counters" class="cl-counters" aria-live="polite"></div>
        </div>
        ${st.media.length ? `<div class="cl-compose-media">${st.media.map((m, i) => `<div class="cl-compose-media-item">
            <div class="cl-media-img is-thumb" aria-hidden="true">🖼<span>${esc(m.label)}</span></div>
            <label><span>대체 텍스트 (description)</span><input type="text" class="tt-search" data-media-alt="${i}" value="${esc(m.alt)}" placeholder="시각 장애가 있는 사람을 위해 이미지를 설명하세요"/></label>
            <button type="button" class="tt-btn" data-media-remove="${i}" aria-label="첨부 제거">✕</button>
          </div>`).join("")}
          <label class="cl-switch-row"><input type="checkbox" id="compose-sensitive" ${st.sensitive ? "checked" : ""}/> 민감한 미디어로 표시 (sensitive)</label></div>` : ""}
        ${st.poll ? `<div class="cl-compose-poll">
          ${st.poll.options.map((o, i) => `<div class="cl-compose-poll-opt"><input type="text" class="tt-search" data-poll-opt="${i}" value="${esc(o)}" placeholder="선택지 ${i + 1}"/>${st.poll.options.length > 2 ? `<button type="button" class="tt-btn" data-poll-remove="${i}" aria-label="선택지 제거">✕</button>` : ""}</div>`).join("")}
          <div class="cl-compose-opts">
            ${st.poll.options.length < 4 ? `<button type="button" class="tt-btn" data-poll-add="1">+ 선택지</button>` : ""}
            <label>기간 <select id="compose-poll-expires" class="tt-btn">${EXPIRES.map(([v, l]) => `<option value="${v}" ${st.poll.expiresIn === v ? "selected" : ""}>${l}</option>`).join("")}</select></label>
            <label><input type="checkbox" id="compose-poll-multiple" ${st.poll.multiple ? "checked" : ""}/> 복수 선택</label>
            <label><input type="checkbox" id="compose-poll-hide" ${st.poll.hideTotals ? "checked" : ""}/> 마감 전 결과 숨기기</label>
          </div>
          ${editOf ? `<small class="cl-handle">투표를 수정하면 기존 투표 결과가 초기화됩니다.</small>` : ""}
        </div>` : ""}
        ${st.scheduleOn ? `<div class="cl-compose-opts"><label>🗓️ 예약 시각 (scheduled_at, 5분 이후) <input type="datetime-local" id="compose-schedule" class="tt-search" value="${esc(st.scheduledAt)}"/></label></div>` : ""}
        <div class="cl-tools" role="toolbar" aria-label="작성 도구">
          <button type="button" class="tt-btn" data-tool="media" ${st.poll || st.media.length >= 4 ? "disabled" : ""} title="${st.poll ? "투표와 미디어는 함께 첨부할 수 없습니다" : "POST /api/v2/media"}">🖼 미디어 ${st.media.length ? `${st.media.length}/4` : ""}</button>
          <button type="button" class="tt-btn ${st.poll ? "is-on" : ""}" data-tool="poll" ${st.media.length || !ap ? "disabled" : ""} title="${!ap ? "투표는 Mastodon 계정에서만" : (st.media.length ? "미디어와 투표는 함께 첨부할 수 없습니다" : "poll[options][]")}">📊 투표</button>
          <button type="button" class="tt-btn ${st.cwOn ? "is-on" : ""}" data-tool="cw" ${ap ? "" : "disabled"} title="열람 주의 (spoiler_text)">⚠ CW</button>
          ${editOf ? "" : `<button type="button" class="tt-btn ${st.scheduleOn ? "is-on" : ""}" data-tool="schedule" ${ap && supports(apAcc, "scheduled") ? "" : "disabled"} title="${ap && !supports(apAcc, "scheduled") ? "연결 서버가 예약 게시를 제공하지 않음" : "scheduled_at"}">🗓️ 예약</button>`}
          <label>언어 <select id="compose-language" class="tt-btn">${Object.entries(LANG).map(([k, l]) => `<option value="${k}" ${st.language === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
          ${ap ? `<label>공개 범위 <select id="compose-visibility" class="tt-btn" ${editOf ? "disabled title=\"게시 후에는 공개 범위를 바꿀 수 없습니다\"" : ""}>
            <option value="public">🌐 공개</option><option value="unlisted">🌙 조용한 공개</option><option value="private">🔒 팔로워 전용</option><option value="direct">✉️ 멘션한 사람만</option>
          </select></label>` : ""}
          ${ap && !editOf ? `<label title="quote_approval_policy">❝ 인용 허용 <select id="compose-quote-policy" class="tt-btn" ${supports(apAcc, "quote") && st.visibility !== "direct" ? "" : "disabled"}>
            <option value="public">누구나</option><option value="followers">팔로워만</option><option value="nobody">나만</option>
          </select></label>` : ""}
        </div>
        ${atNotes.length ? `<div class="cl-compose-note">ⓘ ${atNotes.join(" · ")}</div>` : ""}
        <div class="cl-entity-helper">
          <span>테마 엔티티 → 해시태그 삽입:</span>
          ${theme().existingEntities.slice(0, 8).map((e) => `<button type="button" class="tt-entity-chip resolved" data-insert-tag="${esc(api.hashtagToken(e.name))}" title="[[${esc(e.name)}]] → ${esc(api.hashtagToken(e.name))}">${esc(api.hashtagToken(e.name))}</button>`).join("")}
        </div>
        <details class="pref-like-disclosure">
          <summary>연결 서버 API 요청 미리보기</summary>
          <pre id="compose-preview" class="cl-preview"></pre>
        </details>
        <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:10px;border-top:1px solid var(--border)">
          <button type="button" data-close-dialog="modal-compose" class="tt-btn">취소</button>
          <button type="button" id="compose-submit" class="tt-btn tt-btn-pri">${editOf ? "수정 저장" : (st.scheduleOn ? "예약" : "게시")}</button>
        </div>`;

      const ta = box.querySelector("#compose-text");
      ta.value = st.text;
      ta.addEventListener("input", () => { st.text = ta.value; updateDynamic(); });
      const bindValue = (sel, key, redraw) => {
        const el = box.querySelector(sel);
        if (!el) return;
        if (el.tagName === "SELECT") el.value = st[key];
        el.addEventListener(el.tagName === "SELECT" || el.type === "checkbox" ? "change" : "input", () => {
          st[key] = el.type === "checkbox" ? el.checked : el.value;
          if (redraw) draw(); else updateDynamic();
        });
      };
      bindValue("#compose-cw", "cw");
      bindValue("#compose-visibility", "visibility", true);
      bindValue("#compose-language", "language");
      bindValue("#compose-quote-policy", "quotePolicy");
      bindValue("#compose-sensitive", "sensitive");
      bindValue("#compose-schedule", "scheduledAt");
      box.querySelectorAll("[data-media-alt]").forEach((el) => el.addEventListener("input", () => { st.media[Number(el.getAttribute("data-media-alt"))].alt = el.value; updateDynamic(); }));
      box.querySelectorAll("[data-media-remove]").forEach((el) => el.addEventListener("click", () => { st.media.splice(Number(el.getAttribute("data-media-remove")), 1); draw(); }));
      box.querySelectorAll("[data-poll-opt]").forEach((el) => el.addEventListener("input", () => { st.poll.options[Number(el.getAttribute("data-poll-opt"))] = el.value; updateDynamic(); }));
      box.querySelectorAll("[data-poll-remove]").forEach((el) => el.addEventListener("click", () => { st.poll.options.splice(Number(el.getAttribute("data-poll-remove")), 1); draw(); }));
      const pollAdd = box.querySelector("[data-poll-add]");
      if (pollAdd) pollAdd.addEventListener("click", () => { st.poll.options.push(""); draw(); });
      const pollExp = box.querySelector("#compose-poll-expires");
      if (pollExp) pollExp.addEventListener("change", () => { st.poll.expiresIn = Number(pollExp.value); updateDynamic(); });
      const pollMul = box.querySelector("#compose-poll-multiple");
      if (pollMul) pollMul.addEventListener("change", () => { st.poll.multiple = pollMul.checked; updateDynamic(); });
      const pollHide = box.querySelector("#compose-poll-hide");
      if (pollHide) pollHide.addEventListener("change", () => { st.poll.hideTotals = pollHide.checked; updateDynamic(); });
      box.querySelectorAll("[data-tool]").forEach((b) => b.addEventListener("click", () => {
        const tool = b.getAttribute("data-tool");
        if (tool === "media") st.media.push({ label: `image-${st.media.length + 1}.png`, alt: "" });
        else if (tool === "poll") st.poll = st.poll ? null : { options: ["", ""], expiresIn: 86400, multiple: false, hideTotals: false };
        else if (tool === "cw") st.cwOn = !st.cwOn;
        else if (tool === "schedule") st.scheduleOn = !st.scheduleOn;
        draw();
      }));
      box.querySelectorAll("[data-compose-acc]").forEach((chk) => chk.addEventListener("change", () => {
        const id = chk.getAttribute("data-compose-acc");
        if (chk.checked) selected.add(id); else selected.delete(id);
        draw();
      }));
      box.querySelectorAll("[data-insert-tag]").forEach((b) => b.addEventListener("click", () => {
        st.text = `${st.text}${st.text && !/\s$/.test(st.text) ? " " : ""}${b.getAttribute("data-insert-tag")} `;
        ta.value = st.text;
        ta.focus();
        updateDynamic();
      }));
      box.querySelectorAll("[data-close-dialog]").forEach((b) => b.addEventListener("click", () => modal.close()));
      box.querySelector("#compose-submit").addEventListener("click", submit);
      updateDynamic();
    }

    function pollObject() {
      if (!st.poll) return null;
      const label = (EXPIRES.find(([v]) => v === st.poll.expiresIn) || [0, "1일"])[1];
      return { id: `poll-${Date.now().toString(36)}`, multiple: st.poll.multiple, expires: `${label} 남음`, expired: false, voted: false, ownVotes: [], options: st.poll.options.map((title) => ({ title, votes: 0 })) };
    }

    function submit() {
      const reqs = requests();
      if (!reqs.length) return;
      if (editOf) {
        editOf.history = [...(editOf.history || []), { at: editOf.editedAt || editOf.time, text: editOf.text }];
        Object.assign(editOf, {
          text: st.text, cw: st.cwOn ? st.cw : "", lang: st.language, media: st.media.length ? st.media : undefined, sensitive: st.sensitive,
          poll: st.poll ? pollObject() : undefined, edited: true, editedAt: "방금", hashtags: tagsOf(st.text)
        });
        modal.close();
        announce("✏️ 수정 저장", reqs[0].req);
        cs.selectedPostId = editOf.id;
        api.renderAll();
        return;
      }
      const now = Math.floor(Date.now() / 1000);
      let firstId = null;
      reqs.forEach((r, i) => {
        const acc = r.acc;
        const rkey = `${Date.now().toString(36)}${i}`;
        const scheduled = acc.proto === "ap" && st.scheduleOn;
        const home = acc.timelines.find((t) => t.id === "home" || t.id === "following");
        const timelines = scheduled ? ["scheduled"] : (replyTo ? [] : (st.visibility === "direct" && acc.proto === "ap" ? ["conversations"] : (home ? [home.id] : [])));
        const quoteNative = quote && acc.proto === postProto(quote) && (acc.proto === "at" || supports(acc, "quote"));
        const id = `own_${acc.id}_${rkey}`;
        posts().push({
          id, accountId: acc.id, own: true, unread: false, timelines, replyTo: replyTo ? replyTo.id : null,
          visibility: acc.proto === "ap" ? st.visibility : "public", lang: st.language,
          cw: acc.proto === "ap" && st.cwOn ? st.cw : "", sensitive: st.sensitive,
          media: st.media.length ? st.media.map((m) => ({ kind: "image", label: m.label, alt: m.alt })) : undefined,
          poll: acc.proto === "ap" ? pollObject() || undefined : undefined,
          quoteOf: quoteNative ? quote.id : undefined, quoteState: quoteNative ? "accepted" : undefined,
          quotePolicy: acc.proto === "ap" ? st.quotePolicy : undefined,
          scheduled: scheduled ? { id: `sch-${rkey}`, at: st.scheduledAt.replace("T", " ") } : undefined,
          author: acc.displayName || acc.handle, handle: acc.handle, time: scheduled ? "예약" : "방금", timestamp: scheduled ? now + 86400 : now,
          text: (r.req.body.status || (r.req.body.record && r.req.body.record.text) || st.text),
          hashtags: tagsOf(st.text), card: null,
          url: acc.proto === "ap" ? `https://${acc.server}/@${acc.handle.split("@")[1] || "me"}/${rkey}` : `https://bsky.app/profile/${acc.handle.replace(/^@/, "")}/post/${rkey}`,
          uri: acc.proto === "ap" ? `https://${acc.server}/users/${acc.handle.split("@")[1] || "me"}/statuses/${rkey}` : `at://${acc.did}/app.bsky.feed.post/${rkey}`,
          remoteId: acc.proto === "ap" ? rkey : undefined, cid: acc.proto === "at" ? `bafyrei${rkey}` : undefined,
          counts: { replies: 0, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false
        });
        if (replyTo && acc.id === replyTo.accountId) replyTo.counts.replies += 1;
        if (!firstId) firstId = id;
      });
      modal.close();
      ticker(`${editOf ? "✏️ 수정" : (st.scheduleOn ? "🗓️ 예약" : (replyTo ? "↩ 답글" : (quote ? "❝ 인용" : "✎ 새 글")))} → ${reqs.map((r) => `${r.req.method} ${r.req.url}`).join(" · ")}`);
      if (replyTo) {
        cs.selectedPostId = replyTo.id;
      } else if (firstId) {
        const created = byId(firstId);
        cs.accountId = created.accountId;
        cs.timelineId = created.timelines[0] || cs.timelineId;
        cs.selectedPostId = firstId;
        cs.profile = null;
      }
      api.renderAll();
    }

    draw();
    modal.showModal();
    const ta = box.querySelector("#compose-text");
    if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); }
  }

  /* ── 서버 계정 연결 (OAuth · AT OAuth / 앱 비밀번호) ── */
  function openConnect() {
    api.closeOpenPopovers();
    const modal = document.getElementById("modal-connect");
    const box = document.getElementById("modal-connect-body");
    const isPrivate = !theme().isPublic;
    let proto = "ap";
    let host = "";
    let atAuth = "oauth";
    let allowPublish = !isPrivate;
    /* 프로토타입 전용: 연결 서버가 /api/v2/instance 로 알려주는 제공 기능을 고른다. 서버 종류로 연결을 거부하지 않는다. */
    let serverKind = "mastodon";
    const SERVER_KINDS = {
      mastodon: { label: "Mastodon 5.0 이상 — 기준 기능 전체 제공", software: "Mastodon", version: "5.0.0", missing: [] },
      compat: { label: "Mastodon API 호환 서버 — 일부 기능 미제공", software: "Mastodon API 호환 서버", version: "", missing: ["trends", "quote", "translate", "notifRequests", "scheduled"] }
    };

    function steps() {
      const h = host || (proto === "ap" ? "{server}" : "{handle}");
      const claire = window.location.host || "claire.example.com";
      if (proto === "ap") {
        return [
          `GET https://${h}/api/v2/instance → 서버 소프트웨어·버전, 글자 수 제한, 스트리밍 엔드포인트, 제공 기능 확인 (Mastodon 5.0 기능 기준과 대조해 미제공 기능만 비활성 표시)`,
          `POST https://${h}/api/v1/apps — client_name=Claire Bible, redirect_uris=https://${claire}/syndication/accounts/callback, scopes 등록`,
          `브라우저를 https://${h}/oauth/authorize?response_type=code&code_challenge=…&scope=… 로 이동해 사용자 승인`,
          `POST https://${h}/oauth/token (code + code_verifier) → access_token 을 Claire 서버 테마 DB에 암호화 저장`,
          `GET https://${h}/api/v1/accounts/verify_credentials → 계정 id·핸들 확인`
        ];
      }
      if (atAuth === "oauth") {
        return [
          `핸들 ${h} → DID 해석 (https://${h}/.well-known/atproto-did 또는 DNS TXT _atproto.${h})`,
          "DID 문서 → PDS 엔드포인트 → /.well-known/oauth-protected-resource → 인가 서버 메타데이터",
          `PAR + PKCE + DPoP 로 인가 요청 (client_id = https://${claire}/syndication/oauth/client-metadata.json)`,
          "사용자 승인 후 토큰 교환 → DPoP 바인딩 토큰을 Claire 서버에 암호화 저장",
          "app.bsky.actor.getProfile 로 핸들·DID 확인"
        ];
      }
      return [
        `핸들 ${h} → DID → PDS 엔드포인트 해석`,
        "Claire 서버의 계정 연결 폼(/syndication/accounts)에서만 앱 비밀번호 입력 — 이 프로토타입은 비밀번호를 받거나 저장하지 않음",
        "com.atproto.server.createSession → accessJwt / refreshJwt 를 서버에 암호화 저장",
        "com.atproto.server.refreshSession 으로 주기적 갱신 (OAuth 전환 권장)"
      ];
    }

    function draw() {
      const tpl = api.ACCOUNT_TEMPLATES[proto] || {};
      const scopes = proto === "ap" ? tpl.scopes : (atAuth === "oauth" ? ["atproto", "transition:generic"] : ["app-password"]);
      box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
          <div>
            <h2 id="modal-connect-title" style="margin:0;font-size:16px;font-weight:700">🔌 서버 계정 연결 — ${esc(theme().icon)} ${esc(theme().label)}</h2>
            <p style="margin:4px 0 0;font-size:12px;color:var(--muted)">Claire 는 연합 서버를 직접 구현하지 않고, 기존 서버 계정에 클라이언트로 접속합니다. 연결 계정은 이 테마에만 귀속됩니다.</p>
          </div>
          <button type="button" data-close-dialog="modal-connect" class="tt-btn">✕</button>
        </div>
        <div class="tt-seg" style="display:grid;grid-template-columns:1fr 1fr">
          <button type="button" class="tt-seg-btn ${proto === "ap" ? "active" : ""}" data-connect-proto="ap">🟣 ActivityPub (Mastodon API)</button>
          <button type="button" class="tt-seg-btn ${proto === "at" ? "active" : ""}" data-connect-proto="at">🔵 AT Protocol (PDS)</button>
        </div>
        <div>
          <label for="connect-host" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">${proto === "ap" ? "서버 주소 (Mastodon API 를 제공하는 서버)" : "핸들 (예: name.bsky.social 또는 사용자 도메인)"}</label>
          <input id="connect-host" type="text" class="tt-search" style="width:100%;height:34px" placeholder="${proto === "ap" ? "mastodon.social" : "name.bsky.social"}" value="${esc(host)}"/>
        </div>
        ${proto === "at" ? `<div class="cl-compose-opts"><label>인증 방식
          <select id="connect-at-auth" class="tt-btn">
            <option value="oauth" ${atAuth === "oauth" ? "selected" : ""}>AT OAuth (PAR + DPoP) — 권장</option>
            <option value="app-password" ${atAuth === "app-password" ? "selected" : ""}>앱 비밀번호 (호환 모드)</option>
          </select></label></div>` : `<div class="cl-compose-opts"><label>시뮬레이션 · 서버가 알려주는 제공 기능
          <select id="connect-server-kind" class="tt-btn">
            ${Object.entries(SERVER_KINDS).map(([k, v]) => `<option value="${k}" ${serverKind === k ? "selected" : ""}>${esc(v.label)}</option>`).join("")}
          </select></label></div>
          <p class="cl-handle" style="margin:0">클라이언트 기능은 Mastodon 5.0 기준으로 갖추며, 서버가 제공하지 않는 기능(예: ${esc(SERVER_KINDS.compat.missing.join(", "))})만 비활성으로 표시합니다. 서버 종류 때문에 연결을 거부하지 않습니다.</p>`}
        <div class="cl-scope-row"><span>요청 scope:</span> ${(scopes || []).map((s) => `<code>${esc(s)}</code>`).join(" ")}</div>
        <label class="cl-switch-row"><input id="connect-publish" type="checkbox" ${allowPublish ? "checked" : ""} ${isPrivate ? "disabled" : ""}/> 이 계정으로 발행·부스트 등 쓰기 허용 ${isPrivate ? "<small>(비공개 테마는 읽기 전용 scope 만 요청)</small>" : ""}</label>
        <ol id="connect-flow" class="cl-flow">${steps().map((s) => `<li>${esc(s)}</li>`).join("")}</ol>
        <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:10px;border-top:1px solid var(--border)">
          <button type="button" data-close-dialog="modal-connect" class="tt-btn">취소</button>
          <button type="button" id="connect-submit" class="tt-btn tt-btn-pri">연결 시뮬레이션</button>
        </div>`;

      box.querySelectorAll("[data-connect-proto]").forEach((b) => b.addEventListener("click", () => { proto = b.getAttribute("data-connect-proto"); host = ""; draw(); }));
      const hostInput = box.querySelector("#connect-host");
      hostInput.addEventListener("input", () => {
        host = hostInput.value.trim();
        box.querySelector("#connect-flow").innerHTML = steps().map((s) => `<li>${esc(s)}</li>`).join("");
      });
      const authSel = box.querySelector("#connect-at-auth");
      if (authSel) authSel.addEventListener("change", () => { atAuth = authSel.value; draw(); });
      const kindSel = box.querySelector("#connect-server-kind");
      if (kindSel) kindSel.addEventListener("change", () => { serverKind = kindSel.value; });
      const pub = box.querySelector("#connect-publish");
      pub.addEventListener("change", () => { allowPublish = pub.checked; });
      box.querySelectorAll("[data-close-dialog]").forEach((b) => b.addEventListener("click", () => modal.close()));
      box.querySelector("#connect-submit").addEventListener("click", () => {
        host = hostInput.value.trim();
        submit(scopes);
      });
    }

    function submit(scopes) {
      const raw = host.replace(/^https?:\/\//, "").replace(/\/.*$/, "").replace(/^@/, "").trim();
      if (!raw || !/^[a-z0-9.-]+\.[a-z]{2,}$/i.test(raw)) {
        ticker("⚠️ 올바른 서버 주소 또는 핸들을 입력하세요 (예: mastodon.social, name.bsky.social).");
        return;
      }
      const tpl = JSON.parse(JSON.stringify(api.ACCOUNT_TEMPLATES[proto]));
      const kind = SERVER_KINDS[serverKind];
      const id = `acc_${theme().id}_${proto}_${Date.now().toString(36)}`;
      const writable = !isPrivate && allowPublish;
      const acc = Object.assign(tpl, {
        id, proto,
        software: proto === "ap" ? kind.software : tpl.software,
        serverVersion: proto === "ap" && kind.version ? kind.version : undefined,
        missingFeatures: proto === "ap" ? [...kind.missing] : [],
        server: proto === "ap" ? raw : (raw.endsWith(".bsky.social") ? "bsky.social" : raw),
        handle: proto === "ap" ? `@claire@${raw}` : `@${raw}`,
        did: proto === "at" ? `did:plc:${raw.replace(/[^a-z0-9]/gi, "").slice(0, 12).toLowerCase()}` : undefined,
        displayName: `Claire · ${theme().label}`,
        auth: proto === "ap" ? tpl.auth : (atAuth === "oauth" ? tpl.auth : "앱 비밀번호 (com.atproto.server.createSession)"),
        scopes: writable ? scopes : (proto === "ap" ? ["read"] : scopes),
        canPublish: writable,
        status: (proto === "at" && atAuth !== "oauth") || (proto === "ap" && kind.missing.length) ? "warn" : "ok",
        statusNote: proto === "ap" && kind.missing.length ? `방금 연결됨 — 기준 기능 중 ${kind.missing.length}개 미제공 (비활성)` : "방금 연결됨 (프로토타입 시뮬레이션)"
      });
      api.addAccount(acc);
      const home = acc.timelines[0];
      posts().push({
        id: `welcome_${id}`, accountId: id, timelines: [home.id], unread: true, visibility: "public", lang: "ko",
        author: `${acc.server} 안내`, handle: proto === "ap" ? `@admin@${acc.server}` : "@bsky.app",
        time: "방금", timestamp: Math.floor(Date.now() / 1000),
        text: `${acc.handle} 계정이 Claire Bible 클라이언트와 연결되었습니다. 이제 이 타임라인을 읽고, 게시물을 지식베이스 수집함으로 보내거나 정기 수집 대상으로 등록할 수 있습니다.`,
        hashtags: [], card: null, url: `https://${acc.server}`, uri: `https://${acc.server}/welcome`, remoteId: "1",
        counts: { replies: 0, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false
      });
      modal.close();
      cs.accountId = id;
      cs.timelineId = home.id;
      cs.selectedPostId = null;
      cs.profile = null;
      ticker(`🔌 ${acc.handle} 연결 완료 (${acc.auth})${acc.missingFeatures.length ? ` — 서버 미제공 기능 비활성: ${acc.missingFeatures.join(", ")}` : ""}${acc.canPublish ? "" : " · 읽기 전용"}`);
      api.setAppTab("client");
    }

    draw();
    modal.showModal();
  }

  /* 지식베이스 탭에서 연결 계정으로 발행한 결과를 클라이언트 타임라인에 반영한다. */
  function recordOutbound(item, mode, accs) {
    accs.forEach((acc) => {
      const existing = posts().find((p) => p.accountId === acc.id && !p.notifOnly && (p.kbItemId === item.id || (p.uri && (p.uri === item.apObjectUri || p.uri === item.atUri))));
      if (mode === "boost" && existing) {
        if (!existing.boosted) { existing.boosted = true; existing.counts.boosts += 1; }
        return;
      }
      const home = acc.timelines.find((t) => t.id === "home" || t.id === "following");
      const doc = item.linkedDoc;
      const rkey = Date.now().toString(36);
      posts().push({
        id: `own_${acc.id}_${rkey}`, accountId: acc.id, own: true, unread: false, timelines: home ? [home.id] : [], visibility: "public", lang: "ko",
        author: acc.displayName || acc.handle, handle: acc.handle, time: "방금", timestamp: Math.floor(Date.now() / 1000),
        text: mode === "ingested_create" ? `📡 ${item.title}\n${doc ? doc.summary : item.rawBody}` : `💬 ${item.title}`,
        hashtags: [], card: { title: item.title, description: doc ? doc.summary : item.rawBody, url: item.url },
        url: `https://${acc.server}`, uri: acc.proto === "ap" ? `https://${acc.server}/statuses/${rkey}` : `at://${acc.did}/app.bsky.feed.post/${rkey}`,
        remoteId: acc.proto === "ap" ? rkey : undefined, cid: acc.proto === "at" ? `bafyrei${rkey}` : undefined,
        counts: { replies: 0, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false
      });
    });
  }

  function handleKey(e) {
    if (e.key === "/") { const input = document.getElementById("client-search"); if (input) { e.preventDefault(); input.focus(); } return; }
    if (e.key === "Escape" && cs.profile) { cs.profile = null; api.renderAll(); return; }
    const vis = cs.timelineId === "explore" ? trendPosts(account(cs.accountId)) : visiblePosts();
    const idx = vis.findIndex((p) => p.id === cs.selectedPostId);
    const cur = vis[idx >= 0 ? idx : 0];
    if (e.key === "n") openCompose({});
    else if (e.key === "j" || e.key === "k") {
      if (!vis.length) return;
      const next = vis[e.key === "j" ? Math.min(vis.length - 1, idx + 1) : Math.max(0, idx - 1)] || vis[0];
      selectPost(next.id);
    } else if (!cur || cur.notifOnly) return;
    else if (e.key === "r") openCompose({ replyTo: cur });
    else if (e.key === "q") { if (postProto(cur) === "at" || needFeature(account(cur.accountId), "quote", "인용")) openCompose({ quote: cur }); }
    else if (e.key === "b") toggleAction(cur, "boost");
    else if (e.key === "f") toggleAction(cur, "favourite");
    else if (e.key === "s") sendToKb(cur, false);
    else if (e.key === "i") sendToKb(cur, true);
    else if (e.key === "x" && cur.cw) { toggleKey(cs.revealed, `${cur.id}:cw`); api.renderAll(); }
    else if (e.key === "h" && cur.sensitive) { toggleKey(cs.revealed, `${cur.id}:media`); api.renderAll(); }
    else return;
    e.preventDefault();
  }

  function openPost(postId) {
    const post = byId(postId);
    if (!post) return;
    let anchor = post;
    while (anchor && !(anchor.timelines || []).length && anchor.replyTo) anchor = byId(anchor.replyTo);
    cs.accountId = post.accountId;
    const acc = account(post.accountId);
    cs.timelineId = anchor && (anchor.timelines || [])[0] ? anchor.timelines[0] : (acc && acc.timelines[0] ? acc.timelines[0].id : null);
    cs.search = "";
    cs.profile = null;
    const input = document.getElementById("client-search");
    if (input) input.value = "";
    cs.selectedPostId = postId;
  }

  function openAccount(accId) {
    const acc = account(accId);
    if (!acc) return;
    switchTimeline(accId, timelinesOf(acc)[0] ? timelinesOf(acc)[0].id : null);
  }

  function onThemeChange() {
    cs.accountId = null;
    cs.timelineId = null;
    cs.selectedPostId = null;
    cs.search = "";
    cs.serverQuery = "";
    cs.profile = null;
    const input = document.getElementById("client-search");
    if (input) input.value = "";
  }

  (function bindStatic() {
    const root = panel();
    if (root) {
      root.addEventListener("click", onPanelClick);
      root.addEventListener("change", onPanelChange);
      root.addEventListener("submit", onPanelSubmit);
    }
    const pop = document.getElementById("pop-post-actions");
    if (pop) {
      pop.addEventListener("click", (e) => {
        const b = e.target.closest("[data-cl-menu]");
        if (!b || b.disabled) return;
        try { pop.hidePopover(); } catch (_) {}
        const post = byId(cs.menuPostId);
        if (post) runMenu(b.getAttribute("data-cl-menu"), post);
      });
    }
    const search = document.getElementById("client-search");
    if (search) {
      search.addEventListener("input", (e) => { cs.search = e.target.value; if (cs.timelineId !== "search") render(); });
      search.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && search.value.trim()) {
          e.preventDefault();
          if (cs.timelineId !== "search") cs.prevTimelineId = cs.timelineId;
          const ap = account(cs.accountId) && account(cs.accountId).proto === "ap" ? account(cs.accountId) : (apAccounts()[0] || account(cs.accountId));
          if (ap) cs.accountId = ap.id;
          cs.serverQuery = search.value.trim();
          cs.search = "";
          switchTimeline(cs.accountId, "search");
          ticker(`🔎 GET ${ap ? ap.server : ""}/api/v2/search?q=${encodeURIComponent(cs.serverQuery)}&resolve=true`);
          api.renderAll();
        } else if (e.key === "Escape") {
          search.value = "";
          if (cs.timelineId === "search") closeSearch(); else { cs.search = ""; render(); }
        }
      });
    }
    ["btn-compose", "menu-open-compose"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("click", () => { api.closeOpenPopovers(); openCompose({}); });
    });
    ["btn-connect", "menu-open-connect"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.addEventListener("click", openConnect);
    });
  })();

  return { render, handleKey, title, onThemeChange, openPost, openAccount, findPostForKbItem, recordOutbound, openCompose, openConnect };
};
