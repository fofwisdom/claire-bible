/* Claire Bible — Syndication 연합 클라이언트 탭 (ActivityPub · AT Protocol 연결 서버 계정 경유 클라이언트) */
/*
 * 설계 원칙
 * 1. 서버 경유: Claire 는 Actor/Inbox/HTTP 서명을 직접 운영하지 않고, 테마에 연결된 기존 서버 계정의
 *    Client API(Mastodon 5.0+ REST · AT Protocol PDS XRPC)를 Claire 서버 프록시로 호출한다.
 *    ActivityPub 지원 기준은 Mastodon 5.0 이상이며, 다른 서버 소프트웨어의 게시물은 연결된 Mastodon 서버를 통해 읽는다.
 * 2. 수집 ≠ 적재: 타임라인 게시물은 '수집함 보내기(s)'로 지식베이스 수집 원문이 되고, 엔티티는 적재(i) 시에만 생성된다.
 *    해시태그·facet 태그는 적재 전까지 #수집 태그일 뿐이며 [[Entity]] 로 표시하지 않는다.
 * 3. 결정론적 힌트: 테마 DB 기존 엔티티와 해시태그의 정규화 일치만 표시한다 (LLM 미사용).
 */
window.CLAIRE_SYND_CLIENT_FACTORY = function (api) {
  const { state, prefs, esc, ticker, protoDot } = api;
  const cs = { accountId: null, timelineId: null, selectedPostId: null, search: "" };
  const panel = () => document.getElementById("panel-client");

  function theme() { return api.curTheme(); }
  function posts() { const t = theme(); if (!Array.isArray(t.posts)) t.posts = []; return t.posts; }
  function accounts() { return api.themeAccounts(); }
  function account(id) { return api.findAccount(id); }
  function postProto(post) { const a = account(post.accountId); return a ? a.proto : "ap"; }
  function norm(s) { return String(s || "").toLowerCase().replace(/[\s\-_()#]+/g, ""); }
  function entityMatch(tag) { return theme().existingEntities.find((e) => norm(e.name) === norm(tag)) || null; }

  function ensureSelection() {
    if (cs.timelineId === "sent") return;
    const accs = accounts();
    if (!cs.accountId || !account(cs.accountId)) {
      cs.accountId = accs[0] ? accs[0].id : null;
      cs.timelineId = null;
    }
    const acc = account(cs.accountId);
    if (acc && (!cs.timelineId || !acc.timelines.some((t) => t.id === cs.timelineId))) {
      cs.timelineId = acc.timelines[0] ? acc.timelines[0].id : null;
    }
  }

  function currentTimeline() {
    const acc = account(cs.accountId);
    return acc ? acc.timelines.find((t) => t.id === cs.timelineId) || null : null;
  }

  function visiblePosts() {
    const q = cs.search.trim().toLowerCase();
    let list = cs.timelineId === "sent"
      ? posts().filter((p) => p.kbItemId)
      : posts().filter((p) => p.accountId === cs.accountId && (p.timelines || []).includes(cs.timelineId));
    if (q) {
      list = list.filter((p) => `${p.author} ${p.handle} ${p.text} ${(p.hashtags || []).map((t) => `#${t}`).join(" ")} ${p.card ? `${p.card.title} ${p.card.description}` : ""}`.toLowerCase().includes(q));
    }
    return list.sort((a, b) => b.timestamp - a.timestamp);
  }

  function title() {
    ensureSelection();
    if (cs.timelineId === "sent") return "📥 지식베이스로 보낸 게시물";
    const acc = account(cs.accountId);
    const tl = currentTimeline();
    if (!acc || !tl) return "연합 클라이언트 (연결 계정 없음)";
    return `${tl.icon} ${tl.label} — ${acc.handle}`;
  }

  /* 게시물 ↔ 지식베이스 수집 항목 관계: 직접 보낸 항목 또는 URL/객체 URI 일치 (canonical_url 중복 수집 방지) */
  function kbItemFor(post) {
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
    return posts().find((p) => p.kbItemId === item.id)
      || posts().find((p) => p.uri && (p.uri === item.apObjectUri || p.uri === item.atUri))
      || posts().find((p) => p.url === item.url || (p.card && p.card.url === item.url))
      || null;
  }

  function postTarget(post) {
    return {
      proto: postProto(post), remoteId: post.remoteId, uri: post.uri, cid: post.cid, url: post.url,
      title: post.card ? post.card.title : `${post.author} (${post.handle})`, description: post.text
    };
  }

  function canWrite(acc, kind) {
    if (!acc || !acc.canPublish) return false;
    if (acc.proto === "at") return true;
    const need = { boost: "write:statuses", reply: "write:statuses", quote: "write:statuses", post: "write:statuses", favourite: "write:favourites", bookmark: "write:bookmarks" }[kind];
    return acc.scopes.includes("write") || acc.scopes.includes(need);
  }

  function affinityScore(post) {
    const matches = (post.hashtags || []).filter((t) => entityMatch(t)).length;
    return Math.min(99, 50 + matches * 15 + (post.card ? 10 : 0));
  }

  function textHtml(text) {
    return esc(text)
      .replace(/(^|\s)(#[\p{L}\p{N}_]+)/gu, '$1<span class="cl-hashtag">$2</span>')
      .replace(/(^|\s)(@[\w.-]+(?:@[\w.-]+)?)/g, '$1<span class="cl-mention">$2</span>')
      .replace(/\n/g, "<br/>");
  }

  function initial(name) { return esc((String(name || "?").trim()[0] || "?").toUpperCase()); }

  function notifLine(post) {
    if (!post.notif) return "";
    const label = { mention: "🗨 멘션", reblog: "🔁 부스트", favourite: "★ 좋아요", follow: "👤 팔로우" }[post.notif.kind] || "🔔 알림";
    const tail = { mention: "님이 회원님을 언급했습니다", reblog: "님이 회원님의 게시물을 부스트했습니다", favourite: "님이 회원님의 게시물을 좋아합니다", follow: "님이 회원님을 팔로우합니다" }[post.notif.kind] || "";
    return `<div class="cl-notif">${label} · <b>${esc(post.notif.by)}</b> ${tail}</div>`;
  }

  function kbBadge(post) {
    const rel = kbItemFor(post);
    if (!rel) return "";
    if (rel.item.ingestState === "linked" && rel.item.linkedDoc) {
      return `<span class="tt-link-dot linked" title="지식베이스 적재 완료 (${esc(rel.item.linkedDoc.docId)})${rel.via === "url" ? " · 동일 URL" : ""}">🔗 적재</span>`;
    }
    return `<span class="tt-link-dot cl-inbox" title="지식베이스 수집함에 있음 (미적재 · 엔티티는 적재 시 생성)">📥 수집함</span>`;
  }

  function matchedTagsLine(post) {
    const matched = (post.hashtags || []).filter((t) => entityMatch(t));
    if (!matched.length) return "";
    return `<div class="cl-match-line" title="테마 DB 기존 엔티티와 해시태그 이름이 일치합니다 (결정론적 비교 · 적재 전이므로 엔티티로 연결되지 않음)">🏷️ 테마 엔티티 이름 일치: ${matched.map((t) => `<span class="tt-entity-chip candidate">#${esc(t)} <small style="color:var(--accent)">[일치]</small></span>`).join("")}</div>`;
  }

  function actionBar(post, withLabels) {
    const acc = account(post.accountId);
    const proto = postProto(post);
    const btn = (act, icon, label, on, count, extra) => {
      const kindForScope = act === "fav" ? "favourite" : act;
      const writeAct = ["reply", "boost", "fav", "bookmark", "quote"].includes(act);
      const disabled = writeAct && !canWrite(acc, kindForScope);
      const tip = disabled ? `읽기 전용 연결 계정 (scope: ${acc ? acc.scopes.join(" ") : "-"})` : (extra || label);
      return `<button type="button" class="cl-act ${on ? "on" : ""}" data-cl-act="${act}" data-cl-id="${esc(post.id)}" ${disabled ? "disabled" : ""} title="${esc(tip)}">${icon}${withLabels ? ` <span>${label}</span>` : ""}${count !== undefined ? ` <b>${count}</b>` : ""}</button>`;
    };
    const rel = kbItemFor(post);
    return `<div class="cl-actions">
      ${btn("reply", "💬", "답글", false, post.counts.replies, "답글 (r)")}
      ${btn("boost", "🔁", proto === "at" ? "리포스트" : "부스트", post.boosted, post.counts.boosts, proto === "at" ? "리포스트 (b) · app.bsky.feed.repost" : "부스트 (b) · POST /api/v1/statuses/:id/reblog")}
      ${btn("fav", "★", "좋아요", post.favourited, post.counts.favs, "좋아요 (f)")}
      ${proto === "ap" ? btn("bookmark", "🔖", "북마크", post.bookmarked, undefined, "북마크 · POST /api/v1/statuses/:id/bookmark") : ""}
      ${btn("quote", "❝", "인용", false, undefined, "인용 (q)")}
      <span class="cl-act-sep"></span>
      ${rel ? `<button type="button" class="cl-act kb" data-cl-act="open-kb" data-cl-id="${esc(post.id)}" title="지식베이스 탭에서 수집 항목 열기 (2)">📚${withLabels ? " <span>지식베이스에서 열기</span>" : ""}</button>`
        : `<button type="button" class="cl-act kb" data-cl-act="send" data-cl-id="${esc(post.id)}" title="지식베이스 수집함으로 보내기 (s) — 엔티티는 적재 시 생성">📥${withLabels ? " <span>수집함 보내기</span>" : ""}</button>`}
      ${rel && rel.item.ingestState === "linked" ? "" : `<button type="button" class="cl-act kb" data-cl-act="ingest" data-cl-id="${esc(post.id)}" title="보내고 바로 적재 (i)">⚡${withLabels ? " <span>보내고 적재</span>" : ""}</button>`}
    </div>`;
  }

  function linkCard(card, full) {
    if (!card) return "";
    return `<a class="cl-link-card" href="${esc(card.url)}" target="_blank" rel="noopener noreferrer">
      <span class="cl-link-host">${esc((card.url.match(/^https?:\/\/([^/]+)/) || [])[1] || card.url)}</span>
      <strong>${esc(card.title)}</strong>
      ${card.description ? `<span class="cl-link-desc ${full ? "is-full" : ""}">${esc(card.description)}</span>` : ""}
    </a>`;
  }

  function renderRow(post) {
    const style = state.listStyle;
    const isSel = cs.selectedPostId === post.id;
    const proto = postProto(post);
    if (style === "list") {
      return `<div class="tt-row-wrap ${post.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-cl-post="${esc(post.id)}">
        <div class="tt-flat-row">
          <span class="tt-proto-dot" title="${proto === "at" ? "AT Protocol" : "ActivityPub"}">${protoDot(proto)}</span>
          ${post.notif ? `<span class="cl-notif-icon" title="${esc(post.notif.kind)} · ${esc(post.notif.by)}">🔔</span>` : ""}
          ${kbBadge(post)}
          <div class="tt-flat-main">
            <span class="tt-flat-title">${esc(post.author)}</span>
            <span class="tt-flat-author">${esc(post.handle)}</span>
            <span class="tt-flat-excerpt">— ${esc(post.text)}</span>
          </div>
          <div class="tt-flat-right">
            <span class="cl-counts">💬${post.counts.replies} 🔁${post.counts.boosts} ★${post.counts.favs}</span>
            <span class="tt-flat-time">${esc(post.time)}</span>
            <a class="tt-ext-link" href="${esc(post.url)}" target="_blank" rel="noopener noreferrer" title="원격 서버에서 열기">→</a>
          </div>
        </div>
      </div>`;
    }
    /* 조밀하게: 링크 카드 요약·태그 일치 줄 생략 · 일반: 요약 2줄 · 넓게: 본문과 요약 전체 */
    return `<article class="tt-card-row cl-post ${post.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""}" data-cl-post="${esc(post.id)}">
      ${notifLine(post)}
      <div class="cl-post-head">
        <span class="cl-avatar proto-${proto}" aria-hidden="true">${initial(post.author)}</span>
        <div class="cl-post-who"><b>${esc(post.author)}</b> <span class="cl-handle">${esc(post.handle)}</span></div>
        <div class="tt-card-meta-right">${kbBadge(post)}<span class="tt-card-time">${esc(post.time)}</span></div>
      </div>
      <div class="cl-post-text ${style === "roomy" ? "" : (style === "compact" ? "clamp-2" : "clamp-5")}">${textHtml(post.text)}</div>
      ${style === "compact" ? "" : linkCard(post.card, style === "roomy")}
      ${style === "compact" ? "" : matchedTagsLine(post)}
      ${actionBar(post, style === "roomy")}
    </article>`;
  }

  function threadItem(post, role) {
    const acc = account(post.accountId);
    return `<article class="cl-thread-item is-${role}" ${role === "focus" ? "" : `data-cl-post="${esc(post.id)}"`}>
      ${notifLine(post)}
      <div class="cl-post-head">
        <span class="cl-avatar proto-${acc ? acc.proto : "ap"}" aria-hidden="true">${initial(post.author)}</span>
        <div class="cl-post-who"><b>${esc(post.author)}</b> <span class="cl-handle">${esc(post.handle)}</span></div>
        <div class="tt-card-meta-right"><span class="tt-card-time">${esc(post.time)}</span></div>
      </div>
      <div class="cl-post-text ${role === "focus" ? "is-focus" : ""}">${textHtml(post.text)}</div>
      ${role === "focus" ? linkCard(post.card, true) : ""}
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
    const byId = (id) => posts().find((p) => p.id === id);
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
          <h2 class="tt-art-title">스레드 · ${esc(post.author)}</h2>
          <a class="tt-btn" href="${esc(post.url)}" target="_blank" rel="noopener noreferrer" title="원격 서버의 원본 게시물 열기">원격 서버에서 열기 ↗</a>
        </div>
        <div class="tt-art-meta">
          <span>${protoDot(acc.proto)} 연결 계정 <code>${esc(acc.handle)}</code> 경유 · <code>${esc(threadApi)}</code></span>
        </div>
      </header>
      <div class="cl-thread ${fontCls}" style="--read-fs:${prefs.readerFontSize}px">
        ${ancestors.map((p) => threadItem(p, "ancestor")).join("")}
        ${threadItem(post, "focus")}
        <dl class="cl-meta-grid">
          <dt>${acc.proto === "ap" ? "AP 객체 URI" : "AT URI"}</dt><dd><code>${esc(post.uri || "-")}</code></dd>
          <dt>${acc.proto === "ap" ? "연결 서버 status id" : "레코드 CID"}</dt><dd><code>${esc(acc.proto === "ap" ? (post.remoteId || "-") : (post.cid || "-"))}</code></dd>
        </dl>
        ${actionBar(post, true)}
        ${kbPanel(post)}
        ${replies.length ? `<div class="cl-replies-hdr">답글 ${replies.length}개</div>${replies.map((p) => threadItem(p, "reply")).join("")}` : ""}
      </div>
    </div>`;
  }

  function renderTree() {
    const accs = accounts();
    const host = document.getElementById("cl-tree");
    const sentCnt = posts().filter((p) => p.kbItemId).length;
    host.innerHTML = `<div class="tt-tree-hdr"><span>연결 서버 계정</span><button type="button" class="tt-mini-btn" data-cl-connect="1" title="Mastodon(5.0 이상) 또는 AT Protocol PDS 계정 연결">+ 연결</button></div>
      ${accs.length ? accs.map((acc) => {
        const children = acc.timelines.map((tl) => {
          const cnt = posts().filter((p) => p.accountId === acc.id && (p.timelines || []).includes(tl.id) && p.unread).length;
          const act = cs.accountId === acc.id && cs.timelineId === tl.id ? "active" : "";
          return `<button type="button" class="tt-tree-item ${act}" data-cl-tl="${esc(acc.id)}" data-cl-tl-id="${esc(tl.id)}" title="${esc(tl.api)}"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(tl.icon)} ${esc(tl.label)}</span><span class="tt-count-pill ${cnt > 0 ? "fresh" : ""}">${cnt}</span></button>`;
        }).join("");
        return `<details class="tt-folder" open>
          <summary><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap"><span class="tt-folder-marker"></span>${protoDot(acc.proto)} ${esc(acc.handle)}</span><span class="tt-acct-dot status-${esc(acc.status || "ok")}" title="${esc(acc.statusNote || "")}"></span></summary>
          <div class="cl-acct-meta" title="${esc(acc.auth)} · scope: ${esc(acc.scopes.join(" "))}">${esc(acc.software)}${acc.serverVersion ? ` ${esc(acc.serverVersion)}` : ""} · ${esc(acc.auth.split(" (")[0])}${acc.canPublish ? "" : " · <b>읽기 전용</b>"}</div>
          <div class="tt-tree-children">${children}</div>
        </details>`;
      }).join("") : `<div class="cl-tree-note">이 테마에 연결된 서버 계정이 없습니다. <b>+ 연결</b>로 Mastodon(5.0 이상) 또는 AT Protocol PDS 계정을 연결하세요.</div>`}
      <div class="tt-tree-hdr"><span>지식베이스 연계</span></div>
      <div class="tt-tree-children" style="padding-left:0">
        <button type="button" class="tt-tree-item ${cs.timelineId === "sent" ? "active" : ""}" data-cl-tl="" data-cl-tl-id="sent"><span>📥 수집함으로 보낸 게시물</span><span class="tt-count-pill">${sentCnt}</span></button>
      </div>
      <p class="cl-tree-note">ⓘ Claire 는 연합 서버를 직접 운영하지 않습니다. 각 계정의 서버 API(Mastodon Client API · AT XRPC)로 읽고 쓰며, 접근 토큰은 Claire 서버에만 암호화 보관됩니다.</p>`;

    host.querySelectorAll("[data-cl-tl-id]").forEach((b) => b.addEventListener("click", () => {
      cs.accountId = b.getAttribute("data-cl-tl") || cs.accountId;
      cs.timelineId = b.getAttribute("data-cl-tl-id");
      cs.selectedPostId = null;
      api.renderAll();
    }));
    host.querySelectorAll("[data-cl-connect]").forEach((b) => b.addEventListener("click", openConnect));
  }

  function timelineCall(acc, tl) {
    if (acc.proto === "ap") return tl.api.replace(/^(GET|POST) \//, `$1 ${acc.server}/`);
    return `GET ${acc.server}/xrpc/${tl.api}`;
  }

  function renderStreamHead() {
    const head = document.getElementById("cl-stream-head");
    if (cs.timelineId === "sent") {
      head.innerHTML = `<div class="tt-stream-head-l"><b>📥 지식베이스로 보낸 게시물</b><code>로컬 · 지식베이스 수집함 연계</code></div>`;
      return;
    }
    const acc = account(cs.accountId);
    const tl = currentTimeline();
    if (!acc || !tl) { head.innerHTML = ""; return; }
    const registered = theme().feeds.some((f) => f.via === acc.id && f.timelineId === tl.id);
    head.innerHTML = `<div class="tt-stream-head-l"><b>${esc(tl.icon)} ${esc(tl.label)}</b><code title="연결 서버 API (Claire 서버 프록시 경유)">${esc(timelineCall(acc, tl))}</code></div>
      <div class="tt-stream-head-r">
        <button type="button" class="tt-btn" data-cl-refresh="1" title="연결 서버에서 새 게시물 가져오기">↻ 새로 고침</button>
        ${tl.collectable ? (registered
          ? `<span class="tt-link-dot linked" title="이 타임라인은 지식베이스 정기 수집 대상입니다">✓ 수집 대상 등록됨</span>`
          : `<button type="button" class="tt-btn" data-cl-register="1" title="이 타임라인을 지식베이스 정기 수집 대상으로 등록 (연결 계정 경유)">⇢ 지식베이스 수집 대상 등록</button>`) : ""}
      </div>`;
    const refresh = head.querySelector("[data-cl-refresh]");
    if (refresh) refresh.addEventListener("click", () => {
      const latest = visiblePosts()[0];
      const cursor = acc.proto === "ap" ? `?since_id=${latest && latest.remoteId ? latest.remoteId : ""}` : "?limit=50&cursor=…";
      ticker(`↻ ${timelineCall(acc, tl)}${cursor} → 새 게시물 0건 (프로토타입 시뮬레이션 · 서버 레이트 리밋 헤더 준수)`);
    });
    const reg = head.querySelector("[data-cl-register]");
    if (reg) reg.addEventListener("click", () => registerTimeline(acc, tl));
  }

  function renderStream() {
    const vis = visiblePosts();
    if (!cs.selectedPostId || !vis.some((p) => p.id === cs.selectedPostId)) {
      cs.selectedPostId = vis[0] ? vis[0].id : null;
    }
    const container = document.getElementById("cl-headlines");
    container.innerHTML = vis.length
      ? vis.map(renderRow).join("")
      : `<div style="padding:42px 20px;text-align:center;color:var(--muted);font-size:13px">${accounts().length ? "이 타임라인에 표시할 게시물이 없습니다." : "연결된 서버 계정이 없습니다. 좌측 <b>+ 연결</b>로 계정을 연결하세요."}</div>`;

    const reader = document.getElementById("cl-reader");
    const sel = posts().find((p) => p.id === cs.selectedPostId);
    reader.innerHTML = sel ? buildReader(sel) : `<div style="padding:36px;color:var(--muted);font-size:13px">타임라인에서 게시물을 선택하세요.</div>`;

    const accs = accounts();
    const sentCnt = posts().filter((p) => p.kbItemId).length;
    document.getElementById("footer-stats").textContent = `Theme #${theme().id} · 연결 계정 ${accs.length}개 (${accs.map((a) => `${protoDot(a.proto)} ${a.server}`).join(", ") || "없음"}) · 게시물 ${posts().length}건 · 📥 수집함 보냄 ${sentCnt}건`;
  }

  function bindPanelEvents() {
    const root = panel();
    root.querySelectorAll("[data-cl-post]").forEach((el) => {
      el.addEventListener("click", (e) => {
        if (e.target.closest("button, a, input")) return;
        selectPost(el.getAttribute("data-cl-post"));
      });
    });
    root.querySelectorAll("[data-cl-act]").forEach((b) => {
      b.addEventListener("click", (e) => {
        e.stopPropagation();
        const post = posts().find((p) => p.id === b.getAttribute("data-cl-id"));
        if (post) runAction(b.getAttribute("data-cl-act"), post);
      });
    });
    root.querySelectorAll("[data-cl-search-tag]").forEach((b) => {
      b.addEventListener("click", () => {
        cs.search = `#${b.getAttribute("data-cl-search-tag")}`;
        const input = document.getElementById("client-search");
        if (input) input.value = cs.search;
        api.renderAll();
      });
    });
    root.querySelectorAll(".cl-link-card, .tt-ext-link").forEach((a) => a.addEventListener("click", (e) => e.stopPropagation()));
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
    bindPanelEvents();
  }

  function selectPost(id) {
    cs.selectedPostId = id;
    const post = posts().find((p) => p.id === id);
    if (post && prefs.autoMarkReadOnOpen) post.unread = false;
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
    }
  }

  function toggleAction(post, kind) {
    const acc = account(post.accountId);
    if (!canWrite(acc, kind)) {
      ticker(`🔒 ${acc ? acc.handle : "계정"} 은(는) 이 작업의 쓰기 권한이 없는 읽기 전용 연결 계정입니다.`);
      return;
    }
    const req = api.buildServerRequest(acc, { kind, undo: false, target: postTarget(post) });
    if (!req) { ticker("AT Protocol 연결 계정은 북마크 대신 수집함 보내기(s)를 사용하세요."); return; }
    const field = { boost: "boosted", favourite: "favourited", bookmark: "bookmarked" }[kind];
    const undo = !!post[field];
    const finalReq = undo ? api.buildServerRequest(acc, { kind, undo: true, target: postTarget(post) }) : req;
    post[field] = !undo;
    if (kind === "boost") post.counts.boosts += undo ? -1 : 1;
    if (kind === "favourite") post.counts.favs += undo ? -1 : 1;
    const label = { boost: acc.proto === "at" ? "🔁 리포스트" : "🔁 부스트", favourite: "★ 좋아요", bookmark: "🔖 북마크" }[kind];
    ticker(`${label}${undo ? " 취소" : ""} → ${finalReq.method} ${finalReq.url}${finalReq.body && finalReq.body.collection ? ` (${finalReq.body.collection})` : ""}`);
    api.renderAll();
  }

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
    const firstLine = post.card ? post.card.title : (post.text.split("\n")[0].length > 90 ? `${post.text.split("\n")[0].slice(0, 89)}…` : post.text.split("\n")[0]);
    const item = {
      id: `kb_${post.id}_${Date.now().toString(36)}`, feedId, proto: acc.proto, format: acc.proto === "ap" ? "AP" : "AT-URI", viaAccount: acc.id,
      title: firstLine, author: post.handle, url: post.url,
      apObjectUri: acc.proto === "ap" ? post.uri : undefined, remoteId: acc.proto === "ap" ? post.remoteId : undefined,
      atUri: acc.proto === "at" ? post.uri : undefined, atCid: acc.proto === "at" ? post.cid : undefined,
      timeAgo: post.time, timestamp: post.timestamp, unread: true, starred: false, score: affinityScore(post),
      sourceTags: [...(post.hashtags || [])],
      rawBody: post.text,
      rawHtml: `<p>${textHtml(post.text)}</p>${post.card ? `<blockquote><strong>${esc(post.card.title)}</strong><br/>${esc(post.card.description || "")}<br/><a href="${esc(post.card.url)}" target="_blank" rel="noopener noreferrer">${esc(post.card.url)}</a></blockquote>` : ""}<p style="font-size:0.85em;color:var(--muted)">연결 계정 <code>${esc(acc.handle)}</code> 경유 수집 · <code>${esc(post.uri || post.url)}</code></p>`,
      ingestState: "unlinked", linkedDoc: null, publishState: "none", publishedInfo: null
    };
    theme().items.unshift(item);
    post.kbItemId = item.id;
    return item;
  }

  function sendToKb(post, ingestNow) {
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

  function registerTimeline(acc, tl) {
    const feed = {
      id: `f_tl_${acc.id}_${tl.id.replace(/[^\w-]/g, "_")}`, folder: "연합 타임라인", proto: acc.proto,
      format: acc.proto === "ap" ? "ActivityPub" : "AT Protocol", interval: acc.proto === "ap" ? "stream" : "15m",
      title: `${tl.label} (${acc.handle})`, handle: acc.handle, url: timelineCall(acc, tl), via: acc.id, timelineId: tl.id,
      source: `${tl.label} · ${tl.api}`, etag: "연결 계정 경유 · 등록됨"
    };
    api.addKbFeed(feed);
    let imported = 0;
    posts().filter((p) => p.accountId === acc.id && (p.timelines || []).includes(tl.id)).forEach((p) => {
      if (kbItemFor(p)) return;
      createKbItemFromPost(p, feed.id);
      imported += 1;
    });
    ticker(`⇢ '${tl.label}' 타임라인을 지식베이스 정기 수집 대상으로 등록 (경유: ${acc.handle}) — 첫 수집 ${imported}건, 엔티티는 적재 시 생성됩니다.`);
    api.renderAll();
  }

  /* ── 새 글 · 답글 · 인용 작성 ── */
  function openCompose(ctx) {
    ctx = ctx || {};
    const pubAccs = api.publishAccounts();
    if (!pubAccs.length) {
      ticker("🔒 이 테마에는 글을 쓸 수 있는 연결 계정이 없습니다 (읽기 전용 scope).");
      return;
    }
    const replyTo = ctx.replyTo || null;
    const quote = ctx.quote || null;
    let selected;
    if (replyTo) {
      const a = account(replyTo.accountId);
      if (!canWrite(a, "reply")) { ticker(`🔒 ${a ? a.handle : "계정"} 은(는) 읽기 전용이라 답글을 쓸 수 없습니다.`); return; }
      selected = new Set([a.id]);
    } else if (quote) {
      const same = pubAccs.find((a) => a.proto === postProto(quote));
      selected = new Set([(same || pubAccs[0]).id]);
    } else {
      selected = new Set(pubAccs.map((a) => a.id));
    }
    let text = replyTo && postProto(replyTo) === "ap" ? `${replyTo.handle} ` : "";
    let cw = "";
    let visibility = "public";
    const modal = document.getElementById("modal-compose");
    const box = document.getElementById("modal-compose-body");

    function chosen() { return pubAccs.filter((a) => selected.has(a.id)); }
    function tagsOf(t) { return [...t.matchAll(/#([\p{L}\p{N}_]+)/gu)].map((m) => m[1]); }
    function rootOf(p) { let cur = p; while (cur && cur.replyTo) { const up = posts().find((x) => x.id === cur.replyTo); if (!up) break; cur = up; } return cur; }

    function requests() {
      return chosen().map((acc) => ({
        acc,
        req: api.buildServerRequest(acc, {
          kind: "post", text, cw: acc.proto === "ap" ? cw : "", visibility, tags: tagsOf(text),
          replyTo: replyTo ? postTarget(replyTo) : null,
          replyRoot: replyTo ? postTarget(rootOf(replyTo)) : null,
          quote: quote ? postTarget(quote) : null
        })
      }));
    }

    function updateDynamic() {
      const counters = box.querySelector("#compose-counters");
      let over = false;
      counters.innerHTML = chosen().map((a) => {
        const n = a.proto === "at" ? api.graphemeLength(text) : [...text].length + (cw ? [...cw].length : 0);
        const bad = n > a.charLimit;
        over = over || bad;
        return `<span class="cl-counter ${bad ? "over" : ""}">${protoDot(a.proto)} ${n}/${a.charLimit}${a.proto === "at" ? " graphemes" : "자"}</span>`;
      }).join("");
      box.querySelector("#compose-preview").textContent = requests().map((r) => `# ${r.acc.handle}\n${r.req.method} ${r.req.url}\n${JSON.stringify(r.req.body, null, 2)}`).join("\n\n") || "(계정을 선택하세요)";
      box.querySelector("#compose-submit").disabled = !text.trim() || !chosen().length || over;
    }

    function draw() {
      const ctxPost = replyTo || quote;
      const hasAp = chosen().some((a) => a.proto === "ap");
      const heading = replyTo ? "↩ 답글 작성" : (quote ? "❝ 인용 작성" : "✎ 새 글 작성");
      box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
          <div>
            <h2 id="modal-compose-title" style="margin:0;font-size:16px;font-weight:700">${heading}</h2>
            <p style="margin:4px 0 0;font-size:12px;color:var(--muted)">선택한 연결 계정의 서버 API로 게시합니다 (${replyTo ? "답글은 원 게시물과 같은 계정으로만" : "여러 계정 선택 시 교차 게시"}).</p>
          </div>
          <button type="button" data-close-dialog="modal-compose" class="tt-btn">✕</button>
        </div>
        ${ctxPost ? `<div class="cl-compose-ctx"><div>${quote ? "인용 대상" : "답글 대상"} · <b>${esc(ctxPost.author)}</b> <span class="cl-handle">${esc(ctxPost.handle)}</span></div><div class="cl-post-text clamp-2">${textHtml(ctxPost.text)}</div>${quote && !chosen().every((a) => a.proto === postProto(quote)) ? `<small>다른 프로토콜 계정에는 원문 링크로 인용됩니다.</small>` : ""}</div>` : ""}
        <fieldset class="tt-fieldset">
          <legend>게시할 연결 계정</legend>
          <div class="tt-acc-pick-row">${pubAccs.map((a) => {
            const locked = !!replyTo && a.id !== replyTo.accountId;
            return `<label class="tt-acc-pick ${selected.has(a.id) ? "on" : ""} ${locked ? "locked" : ""}"><input type="checkbox" data-compose-acc="${esc(a.id)}" ${selected.has(a.id) ? "checked" : ""} ${locked || replyTo ? "disabled" : ""}/><span>${protoDot(a.proto)} <b>${esc(a.handle)}</b> <small>${esc(a.software)}</small></span></label>`;
          }).join("")}</div>
        </fieldset>
        <div>
          <label for="compose-text" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">본문</label>
          <textarea id="compose-text" class="cl-compose-text" rows="5" placeholder="무엇을 공유할까요? #해시태그는 AP Hashtag · AT facet 태그로 변환됩니다."></textarea>
          <div id="compose-counters" class="cl-counters" aria-live="polite"></div>
        </div>
        ${hasAp ? `<div class="cl-compose-opts">
          <label>공개 범위 (AP)
            <select id="compose-visibility" class="tt-btn">
              <option value="public">공개 (public)</option>
              <option value="unlisted">조용한 공개 (unlisted)</option>
              <option value="private">팔로워 전용 (private)</option>
              <option value="direct">멘션한 사람만 (direct)</option>
            </select>
          </label>
          <label>열람 주의 CW (AP)<input id="compose-cw" type="text" class="tt-search" style="width:220px" placeholder="spoiler_text (선택)"/></label>
        </div>` : ""}
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
          <button type="button" id="compose-submit" class="tt-btn tt-btn-pri">게시</button>
        </div>`;

      const ta = box.querySelector("#compose-text");
      ta.value = text;
      ta.addEventListener("input", () => { text = ta.value; updateDynamic(); });
      const vis = box.querySelector("#compose-visibility");
      if (vis) { vis.value = visibility; vis.addEventListener("change", () => { visibility = vis.value; updateDynamic(); }); }
      const cwInput = box.querySelector("#compose-cw");
      if (cwInput) { cwInput.value = cw; cwInput.addEventListener("input", () => { cw = cwInput.value; updateDynamic(); }); }
      box.querySelectorAll("[data-compose-acc]").forEach((chk) => chk.addEventListener("change", () => {
        const id = chk.getAttribute("data-compose-acc");
        if (chk.checked) selected.add(id); else selected.delete(id);
        draw();
      }));
      box.querySelectorAll("[data-insert-tag]").forEach((b) => b.addEventListener("click", () => {
        const tok = b.getAttribute("data-insert-tag");
        text = `${text}${text && !/\s$/.test(text) ? " " : ""}${tok} `;
        ta.value = text;
        ta.focus();
        updateDynamic();
      }));
      box.querySelectorAll("[data-close-dialog]").forEach((b) => b.addEventListener("click", () => modal.close()));
      box.querySelector("#compose-submit").addEventListener("click", submit);
      updateDynamic();
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    }

    function submit() {
      const reqs = requests();
      if (!reqs.length || !text.trim()) return;
      const now = Math.floor(Date.now() / 1000);
      let firstId = null;
      reqs.forEach((r, i) => {
        const acc = r.acc;
        const home = acc.timelines.find((t) => t.id === "home" || t.id === "following");
        const id = `own_${acc.id}_${Date.now().toString(36)}_${i}`;
        const rkey = Date.now().toString(36);
        posts().push({
          id, accountId: acc.id, own: true, unread: false,
          timelines: replyTo ? [] : (home ? [home.id] : []), replyTo: replyTo ? replyTo.id : null,
          author: acc.displayName || acc.handle, handle: acc.handle, time: "방금", timestamp: now,
          text: r.req.body.status || (r.req.body.record && r.req.body.record.text) || text,
          hashtags: tagsOf(text), card: null,
          url: acc.proto === "ap" ? `https://${acc.server}/${acc.handle.split("@")[1] ? `@${acc.handle.split("@")[1]}` : ""}/${rkey}` : `https://bsky.app/profile/${acc.handle.replace(/^@/, "")}/post/${rkey}`,
          uri: acc.proto === "ap" ? `https://${acc.server}/statuses/${rkey}` : `at://${acc.did}/app.bsky.feed.post/${rkey}`,
          remoteId: acc.proto === "ap" ? rkey : undefined, cid: acc.proto === "at" ? `bafyrei${rkey}` : undefined,
          counts: { replies: 0, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false
        });
        if (replyTo && acc.id === replyTo.accountId) replyTo.counts.replies += 1;
        if (!firstId) firstId = id;
      });
      modal.close();
      ticker(`${replyTo ? "↩ 답글" : (quote ? "❝ 인용" : "✎ 새 글")} 게시 → ${reqs.map((r) => `${r.req.method} ${r.req.url}`).join(" · ")}`);
      if (replyTo) cs.selectedPostId = replyTo.id;
      else if (firstId) { cs.accountId = reqs[0].acc.id; const home = reqs[0].acc.timelines.find((t) => t.id === "home" || t.id === "following"); if (home) cs.timelineId = home.id; cs.selectedPostId = firstId; }
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
    /* 프로토타입 전용: 연결 대상 서버가 GET /api/v2/instance 에서 응답할 version 을 골라 지원 기준 검사를 시연한다. */
    let simVersion = "5.0.0";
    const SIM_VERSIONS = [
      { value: "5.0.0", label: "5.0.0 — Mastodon 5.0 (지원)" },
      { value: "5.0.0+glitch", label: "5.0.0+glitch — Mastodon 포크 (지원)" },
      { value: "4.5.2", label: "4.5.2 — Mastodon 4.5 (기준 미달)" },
      { value: "2.7.2 (compatible; Pleroma 2.7.0)", label: "2.7.2 (compatible; …) — 호환 표기 서버 (미지원)" },
      { value: "0.19.0", label: "0.19.0 — Mastodon 이외 서버 (미지원)" }
    ];

    function steps() {
      const h = host || (proto === "ap" ? "{server}" : "{handle}");
      const claire = window.location.host || "claire.example.com";
      if (proto === "ap") {
        return [
          `GET https://${h}/api/v2/instance → version 이 Mastodon 5.0.0 이상인지 확인 (미달·호환 표기 서버는 여기서 중단), 글자 수 제한·스트리밍 엔드포인트 확인`,
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
      const scopes = proto === "ap"
        ? ["read", "write:statuses", "write:favourites", "write:bookmarks", "write:follows"]
        : (atAuth === "oauth" ? ["atproto", "transition:generic"] : ["app-password"]);
      box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
          <div>
            <h2 id="modal-connect-title" style="margin:0;font-size:16px;font-weight:700">🔌 서버 계정 연결 — ${esc(theme().icon)} ${esc(theme().label)}</h2>
            <p style="margin:4px 0 0;font-size:12px;color:var(--muted)">Claire 는 연합 서버를 직접 구현하지 않고, 기존 서버 계정에 클라이언트로 접속합니다. 연결 계정은 이 테마에만 귀속됩니다.</p>
          </div>
          <button type="button" data-close-dialog="modal-connect" class="tt-btn">✕</button>
        </div>
        <div class="tt-seg" style="display:grid;grid-template-columns:1fr 1fr">
          <button type="button" class="tt-seg-btn ${proto === "ap" ? "active" : ""}" data-connect-proto="ap">🟣 ActivityPub (Mastodon 5.0+)</button>
          <button type="button" class="tt-seg-btn ${proto === "at" ? "active" : ""}" data-connect-proto="at">🔵 AT Protocol (PDS)</button>
        </div>
        <div>
          <label for="connect-host" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">${proto === "ap" ? "Mastodon 서버 주소 (5.0 이상)" : "핸들 (예: name.bsky.social 또는 사용자 도메인)"}</label>
          <input id="connect-host" type="text" class="tt-search" style="width:100%;height:34px" placeholder="${proto === "ap" ? "mastodon.social" : "name.bsky.social"}" value="${esc(host)}"/>
        </div>
        ${proto === "at" ? `<div class="cl-compose-opts"><label>인증 방식
          <select id="connect-at-auth" class="tt-btn">
            <option value="oauth" ${atAuth === "oauth" ? "selected" : ""}>AT OAuth (PAR + DPoP) — 권장</option>
            <option value="app-password" ${atAuth === "app-password" ? "selected" : ""}>앱 비밀번호 (호환 모드)</option>
          </select></label></div>` : `<div class="cl-compose-opts"><label>시뮬레이션 · 서버 응답 version
          <select id="connect-sim-version" class="tt-btn">
            ${SIM_VERSIONS.map((v) => `<option value="${esc(v.value)}" ${v.value === simVersion ? "selected" : ""}>${esc(v.label)}</option>`).join("")}
          </select></label></div>`}
        <div id="connect-error" class="cl-connect-error" role="alert" hidden></div>
        <div class="cl-scope-row"><span>요청 scope:</span> ${scopes.map((s) => `<code>${esc(s)}</code>`).join(" ")}</div>
        <label class="cl-switch-row"><input id="connect-publish" type="checkbox" ${allowPublish ? "checked" : ""} ${isPrivate ? "disabled" : ""}/> 이 계정으로 발행·부스트 허용 ${isPrivate ? "<small>(비공개 테마는 읽기 전용 scope 만 요청)</small>" : ""}</label>
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
      const verSel = box.querySelector("#connect-sim-version");
      if (verSel) verSel.addEventListener("change", () => { simVersion = verSel.value; box.querySelector("#connect-error").hidden = true; });
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
      let serverVersion;
      if (proto === "ap") {
        const gate = api.checkMastodonBaseline(simVersion);
        if (!gate.ok) {
          const err = box.querySelector("#connect-error");
          err.textContent = `연결 거부: ${gate.reason}. ActivityPub 클라이언트 기능은 Mastodon 5.0 이상 서버만 지원합니다.`;
          err.hidden = false;
          ticker(`⛔ ${raw} 연결 거부 — ${gate.reason}`);
          return;
        }
        serverVersion = gate.version;
      }
      const tpl = api.ACCOUNT_TEMPLATES[proto];
      const id = `acc_${theme().id}_${proto}_${Date.now().toString(36)}`;
      const acc = {
        id, proto, software: tpl.software, serverVersion,
        server: proto === "ap" ? raw : (raw.endsWith(".bsky.social") ? "bsky.social" : raw),
        handle: proto === "ap" ? `@claire@${raw}` : `@${raw}`,
        did: proto === "at" ? `did:plc:${raw.replace(/[^a-z0-9]/gi, "").slice(0, 12).toLowerCase()}` : undefined,
        displayName: `Claire · ${theme().label}`,
        auth: proto === "ap" ? tpl.auth : (atAuth === "oauth" ? tpl.auth : "앱 비밀번호 (com.atproto.server.createSession)"),
        scopes: isPrivate ? (proto === "ap" ? ["read"] : scopes) : (allowPublish ? scopes : (proto === "ap" ? ["read"] : scopes)),
        canPublish: !isPrivate && allowPublish,
        status: proto === "at" && atAuth !== "oauth" ? "warn" : "ok",
        statusNote: "방금 연결됨 (프로토타입 시뮬레이션)",
        charLimit: tpl.charLimit,
        timelines: JSON.parse(JSON.stringify(tpl.timelines))
      };
      api.addAccount(acc);
      const home = acc.timelines[0];
      posts().push({
        id: `welcome_${id}`, accountId: id, timelines: [home.id], unread: true,
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
      ticker(`🔌 ${acc.handle} 연결 완료 (${acc.auth}) — scope: ${acc.scopes.join(" ")}${acc.canPublish ? "" : " · 읽기 전용"}`);
      api.setAppTab("client");
    }

    draw();
    modal.showModal();
  }

  /* 지식베이스 탭에서 연결 계정으로 발행한 결과를 클라이언트 타임라인에 반영한다. */
  function recordOutbound(item, mode, accs) {
    accs.forEach((acc) => {
      const existing = posts().find((p) => p.accountId === acc.id && (p.kbItemId === item.id || (p.uri && (p.uri === item.apObjectUri || p.uri === item.atUri))));
      if (mode === "boost" && existing) {
        if (!existing.boosted) { existing.boosted = true; existing.counts.boosts += 1; }
        return;
      }
      const home = acc.timelines.find((t) => t.id === "home" || t.id === "following");
      const doc = item.linkedDoc;
      const rkey = Date.now().toString(36);
      posts().push({
        id: `own_${acc.id}_${rkey}`, accountId: acc.id, own: true, unread: false, timelines: home ? [home.id] : [],
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
    const vis = visiblePosts();
    const idx = vis.findIndex((p) => p.id === cs.selectedPostId);
    const cur = vis[idx >= 0 ? idx : 0];
    if (e.key === "n") openCompose({});
    else if (e.key === "j" || e.key === "k") {
      if (!vis.length) return;
      const next = vis[e.key === "j" ? Math.min(vis.length - 1, idx + 1) : Math.max(0, idx - 1)] || vis[0];
      selectPost(next.id);
    } else if (!cur) return;
    else if (e.key === "r") openCompose({ replyTo: cur });
    else if (e.key === "q") openCompose({ quote: cur });
    else if (e.key === "b") toggleAction(cur, "boost");
    else if (e.key === "f") toggleAction(cur, "favourite");
    else if (e.key === "s") sendToKb(cur, false);
    else if (e.key === "i") sendToKb(cur, true);
    else return;
    e.preventDefault();
  }

  function openPost(postId) {
    const post = posts().find((p) => p.id === postId);
    if (!post) return;
    let anchor = post;
    while (anchor && !(anchor.timelines || []).length && anchor.replyTo) anchor = posts().find((p) => p.id === anchor.replyTo);
    cs.accountId = post.accountId;
    const acc = account(post.accountId);
    cs.timelineId = anchor && (anchor.timelines || [])[0] ? anchor.timelines[0] : (acc && acc.timelines[0] ? acc.timelines[0].id : null);
    cs.search = "";
    const input = document.getElementById("client-search");
    if (input) input.value = "";
    cs.selectedPostId = postId;
  }

  function openAccount(accId) {
    const acc = account(accId);
    if (!acc) return;
    cs.accountId = accId;
    cs.timelineId = acc.timelines[0] ? acc.timelines[0].id : null;
    cs.selectedPostId = null;
  }

  function onThemeChange() {
    cs.accountId = null;
    cs.timelineId = null;
    cs.selectedPostId = null;
    cs.search = "";
    const input = document.getElementById("client-search");
    if (input) input.value = "";
  }

  (function bindStatic() {
    const search = document.getElementById("client-search");
    if (search) search.addEventListener("input", (e) => { cs.search = e.target.value; render(); });
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
