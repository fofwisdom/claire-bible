/* Claire Bible — Syndication 다른 사용자 발행 본문 (연합 클라이언트 · 지식베이스 두 탭 공용, 제안) */
/*
 * 설계 원칙 (docs/design/EXTERNAL_IDP_AND_PER_USER_WORKSPACE_DESIGN.md §4.7)
 * 1. 격리 유지: 다른 사용자의 워크스페이스 DB 는 읽지 않는다. 공개 발행 때 만들어지는 공개 사본만
 *    인스턴스 공용 발행 색인(GET /api/peers/publications)에서 조회한다.
 * 2. 내 계정으로 발행: 부스트 · 인용 · 링크 공유는 지금 테마의 연결 계정 서버 API 로 보낸다.
 *    부스트와 네이티브 인용은 원 작성자가 같은 프로토콜로 발행한 게시물이 있어야 하며, 없으면 링크 공유로 대체한다.
 * 3. 가져오기 = 수집: 가져온 본문은 내 지식베이스의 수집 원문이 된다. 원 작성자의 엔티티 이름은 #수집 태그로만 남고,
 *    엔티티는 내 테마 DB 에 적재할 때 생성된다 (적재 LLM 비용은 내 예산).
 * 4. 작성자 동의: 인용 · 가져오기는 원 작성자가 정한 재사용 범위(reuse)를 따른다. 철회된 본문은 목록에서 빠지고,
 *    내가 이미 가져오거나 재발행한 경우에만 '철회됨'으로 남는다.
 */
window.CLAIRE_SYND_PEERS_FACTORY = function (api) {
  const { esc, ticker, protoDot, prefs, state } = api;
  const DATA = window.CLAIRE_SYND_PEERS || { viewerSeq: 0, indexApi: "GET /api/peers/publications", users: [], publications: [] };
  const ps = { clientUser: "all", selectedId: null };
  const ledger = {};
  const IMPORT_FEED_ID = "fpeer-import";
  const MODES = {
    boost: { icon: "🔁", label: "부스트" },
    quote: { icon: "❝", label: "인용 발행" },
    link: { icon: "✎", label: "링크 공유" },
    import_publish: { icon: "📡", label: "가져와 적재 발행" }
  };
  const VIEW_LABEL = "👥 다른 사용자 발행 본문";

  function theme() { return api.curTheme(); }
  function user(seq) { return DATA.users.find((u) => u.seq === seq) || null; }
  function pub(id) { return DATA.publications.find((p) => p.id === id) || null; }
  function originUrl(p) { return `${window.location.origin}${p.shareUrl}`; }
  function initial(name) { return esc((String(name || "?").trim()[0] || "?").toUpperCase()); }
  function norm(s) { return String(s || "").toLowerCase().replace(/[\s\-_()#]+/g, ""); }
  function ledgerKey(p) { return `${theme().id}:${p.id}`; }
  function myLog(p) { return ledger[ledgerKey(p)] || []; }
  function logAct(p, entry) { (ledger[ledgerKey(p)] = ledger[ledgerKey(p)] || []).push({ at: "방금", ...entry }); }
  function importedItem(p) { return theme().items.find((it) => it.peerPubId === p.id) || null; }
  function reshareCount(p) { return myLog(p).filter((x) => x.mode !== "import").length; }
  function touched(p) { return !!importedItem(p) || reshareCount(p) > 0; }
  function writable() { return api.publishAccounts(); }
  function samePost(p, acc) { return p.posts.find((x) => x.proto === acc.proto) || null; }
  function myPostFor(p) {
    const uris = p.posts.map((x) => x.uri);
    return (theme().posts || []).find((x) => !x.own && uris.includes(x.uri)) || null;
  }
  function entityMatches(p) {
    const mine = theme().existingEntities || [];
    return p.entities.filter((e) => mine.some((m) => norm(m.name) === norm(e.name)));
  }

  /* 색인 조회 규칙: 내 발행 제외 · 정지 · 공유를 끈 사용자 제외 · 철회 본문은 내가 손댄 경우에만 표시 */
  function listable(p) {
    const u = user(p.userSeq);
    if (!u || p.userSeq === DATA.viewerSeq) return false;
    if (u.status !== "active" || !u.shareListed) return false;
    if (p.retracted && !touched(p)) return false;
    return true;
  }
  function hiddenNote() {
    const own = DATA.publications.filter((p) => p.userSeq === DATA.viewerSeq).length;
    const suspended = DATA.publications.filter((p) => { const u = user(p.userSeq); return u && p.userSeq !== DATA.viewerSeq && u.status !== "active"; }).length;
    const retracted = DATA.publications.filter((p) => p.retracted && !touched(p)).length;
    return [own ? `내 발행 ${own}건 제외` : "", suspended ? `정지 사용자 ${suspended}건 숨김` : "", retracted ? `철회 ${retracted}건` : ""].filter(Boolean).join(" · ");
  }
  function visible(userKey) {
    const key = String(userKey === undefined ? "all" : userKey);
    return DATA.publications
      .filter(listable)
      .filter((p) => key === "all" || String(p.userSeq) === key)
      .sort((a, b) => b.timestamp - a.timestamp);
  }
  function peerUsers() {
    return DATA.users.filter((u) => u.seq !== DATA.viewerSeq && visible(u.seq).length > 0);
  }
  function selected(userKey) {
    const vis = visible(userKey);
    let p = vis.find((x) => x.id === ps.selectedId);
    if (!p) { p = vis[0] || null; ps.selectedId = p ? p.id : null; }
    return p;
  }
  function unreadCount(userKey) { return visible(userKey).filter((p) => p.unread).length; }
  function pubForUri(uri) {
    if (!uri) return null;
    return DATA.publications.find((p) => listable(p) && p.posts.some((x) => x.uri === uri)) || null;
  }

  function modeAllowed(p, mode) {
    if (p.retracted) return { ok: false, why: "원 작성자가 발행을 철회했습니다" };
    if (mode === "quote" && !p.reuse.quote) return { ok: false, why: "원 작성자가 인용을 허용하지 않았습니다" };
    if (mode === "import_publish" && !p.reuse.import && !importedItem(p)) return { ok: false, why: "원 작성자가 가져오기를 허용하지 않았습니다" };
    if (mode === "boost" && !p.posts.length) return { ok: false, why: "연합우주 게시물 없이 Claire 공유 링크로만 발행된 본문입니다" };
    return { ok: true, why: "" };
  }

  /* ── 트리 ── */
  function treeHtml(tab, activeKey) {
    const btn = (key, label, sub, cnt, total) => {
      const act = String(activeKey) === String(key) ? "active" : "";
      const attrs = tab === "kb"
        ? `data-tree-kind="peer" data-tree-val="${esc(key)}" data-tree-lbl="${esc(key === "all" ? VIEW_LABEL : `👥 ${label}`)}"`
        : `data-peer-view="client" data-peer-user="${esc(key)}"`;
      return `<button type="button" class="tt-tree-item ${act}" ${attrs} title="${esc(`${DATA.indexApi}${key === "all" ? "" : `?user=${key}`} · 인스턴스 공용 발행 색인 (공개 발행만)`)}"${key === "all" ? "" : ` style="padding-left:22px"`}><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${key === "all" ? "👥" : `<span class="cl-avatar is-small peer-avatar" aria-hidden="true">${initial(label)}</span>`} ${esc(label)}${sub ? ` <small class="tt-via">${esc(sub)}</small>` : ""}</span><span class="tt-count-pill ${cnt > 0 ? "fresh" : ""}">${cnt || total || ""}</span></button>`;
    };
    const users = peerUsers();
    return btn("all", "다른 사용자 발행 본문", "", unreadCount("all"), visible("all").length)
      + users.map((u) => btn(u.seq, u.name, u.bio || "", unreadCount(u.seq), visible(u.seq).length)).join("");
  }

  function streamHeadHtml(userKey) {
    const u = String(userKey) === "all" ? null : user(Number(userKey));
    return `<div class="tt-stream-head-l"><b>${u ? `👥 ${esc(u.name)}` : VIEW_LABEL}</b><span class="peer-proposal">제안</span><code title="다른 사용자의 워크스페이스 DB 가 아니라 공개 사본만 담은 공용 색인을 조회합니다">${esc(DATA.indexApi)}${u ? `?user=${u.seq}` : ""}</code></div>
      <div class="tt-stream-head-r"><span class="cl-meta-chip" title="공개 발행만 올라가며, 사용자는 공유를 끌 수 있습니다">${esc(hiddenNote() || "공개 발행만")}</span></div>`;
  }

  /* ── 목록 행 (두 탭 공통 · 목록 스타일 4종) ── */
  function whereDots(p) {
    return p.posts.length ? p.posts.map((x) => protoDot(x.proto)).join("") : "🔗";
  }
  function badges(p) {
    const imp = importedItem(p);
    const cnt = reshareCount(p);
    return [
      p.retracted ? `<span class="peer-badge is-retracted" title="원 작성자가 발행을 철회했습니다">철회됨</span>` : "",
      imp ? `<span class="tt-link-dot ${imp.ingestState === "linked" ? "linked" : "cl-inbox"}" title="내 지식베이스 ${imp.ingestState === "linked" ? "적재 완료" : "수집함"} (${esc(imp.id)})">${imp.ingestState === "linked" ? "🔗 가져와 적재" : "📥 가져옴"}</span>` : "",
      cnt ? `<span class="peer-badge is-mine" title="이 테마의 연결 계정으로 재발행한 횟수">🔁 내 재발행 ${cnt}</span>` : ""
    ].join("");
  }
  function entityChip(e, matches) {
    const hit = matches.some((m) => norm(m.name) === norm(e.name));
    return `<span class="tt-entity-chip candidate" title="${hit ? "원 작성자의 엔티티 — 내 테마 DB 의 같은 이름 엔티티와 일치 (가져와 적재하면 바로 연계)" : "원 작성자의 엔티티 — 가져오면 #수집 태그, 적재하면 내 테마 엔티티로 생성"}">[[${esc(e.name)}]]${hit ? " <small style='color:var(--accent)'>[일치]</small>" : ""}</span>`;
  }
  function quickActions(p) {
    const pubOk = writable().length > 0 && !p.retracted;
    const imp = importedItem(p);
    return `<button type="button" class="tt-btn" style="height:23px;padding:0 7px;font-size:11px" data-peer-act="reshare" data-peer-id="${esc(p.id)}" ${pubOk ? "" : `disabled title="${esc(p.retracted ? "철회된 본문" : "쓰기 권한이 있는 연결 계정이 없습니다")}"`}>🔁 내 계정으로 발행</button>
      ${imp
        ? `<button type="button" class="tt-btn" style="height:23px;padding:0 7px;font-size:11px" data-peer-act="open-kb" data-peer-id="${esc(p.id)}">📚 열기</button>`
        : `<button type="button" class="tt-btn" style="height:23px;padding:0 7px;font-size:11px" data-peer-act="import" data-peer-id="${esc(p.id)}" ${p.reuse.import && !p.retracted ? "" : `disabled title="${esc(p.retracted ? "철회된 본문" : "원 작성자가 가져오기를 허용하지 않았습니다")}"`}>📥 가져오기</button>`}`;
  }
  function rowHtml(p) {
    const u = user(p.userSeq);
    const isSel = ps.selectedId === p.id;
    const style = state.listStyle;
    const cls = `${p.unread ? "is-unread" : ""} ${isSel ? "is-selected" : ""} ${p.retracted ? "is-retracted" : ""}`;
    if (style === "list") {
      return `<div class="tt-row-wrap peer-row ${cls}" data-peer-open="${esc(p.id)}">
        <div class="tt-flat-row">
          <span class="tt-proto-dot" title="원 작성자가 발행한 위치${p.posts.length ? "" : " (Claire 공유 링크만)"}">${whereDots(p)}</span>
          ${badges(p)}
          <div class="tt-flat-main">
            <span class="tt-flat-title">${esc(p.title)}</span>
            <span class="tt-flat-author">${esc(u.name)}</span>
            ${prefs.showExcerpt ? `<span class="tt-flat-excerpt">— ${esc(p.summary)}</span>` : ""}
          </div>
          <div class="tt-flat-right">
            <span class="tt-feed-pill proto-claire">${esc(p.themeLabel)}</span>
            <span class="tt-flat-time">${esc(p.publishedAt)}</span>
            <a class="tt-ext-link" href="${esc(originUrl(p))}" target="_blank" rel="noopener noreferrer" title="공개 공유 페이지 열기">→</a>
          </div>
        </div>
      </div>`;
    }
    const matches = entityMatches(p);
    let summary = "";
    if (style === "card") summary = `<p class="tt-card-snippet">${esc(p.summary)}</p>`;
    else if (style === "roomy") summary = `<p class="tt-card-snippet is-full">${esc(p.summary)}</p>`;
    return `<article class="tt-card-row peer-row ${cls}" data-peer-open="${esc(p.id)}">
      <div class="tt-card-topbar">
        <div class="tt-card-meta-left">
          <span class="cl-avatar is-small peer-avatar" aria-hidden="true">${initial(u.name)}</span>
          <span class="tt-card-feed-title">${esc(u.name)}</span>
          <span class="tt-feed-pill proto-claire">${esc(p.themeLabel)}</span>
        </div>
        <div class="tt-card-meta-right">
          ${badges(p)}
          <span title="원 작성자가 발행한 위치">${whereDots(p)}</span>
          <span class="tt-card-time">${esc(p.publishedAt)}</span>
        </div>
      </div>
      <h3 class="tt-card-title">${esc(p.title)}</h3>
      ${summary}
      <div class="tt-card-bottom">
        <div class="tt-card-tags">${style === "compact" ? "" : p.entities.map((e) => entityChip(e, matches)).join("")}</div>
        <div class="tt-card-actions">${quickActions(p)}</div>
      </div>
    </article>`;
  }

  function streamHtml(userKey) {
    selected(userKey);
    const vis = visible(userKey);
    if (!vis.length) return `<div class="cl-empty">다른 사용자가 공개 발행한 본문이 없습니다.</div>`;
    return vis.map(rowHtml).join("");
  }

  /* ── 리더 (두 탭 공통) ── */
  function reuseChips(p) {
    const chip = (ok, label, tip) => `<span class="peer-reuse-chip ${ok ? "ok" : "no"}" title="${esc(tip)}">${ok ? "✓" : "✕"} ${esc(label)}</span>`;
    return `<div class="peer-reuse" aria-label="원 작성자의 재사용 범위">
      ${chip(true, "부스트 · 링크 공유", "공개 발행이면 항상 허용")}
      ${chip(p.reuse.quote, "인용", p.reuse.quote ? "원 작성자가 인용을 허용했습니다 (연합우주에서는 원 게시물의 인용 허용 범위도 따름)" : "원 작성자가 인용을 허용하지 않았습니다")}
      ${chip(p.reuse.import, "가져오기", p.reuse.import ? "내 지식베이스로 가져와 적재 · 재가공할 수 있습니다" : "원 작성자가 가져오기를 허용하지 않았습니다")}
      ${p.license ? `<span class="peer-reuse-chip" title="원 작성자가 붙인 이용 허락">${esc(p.license)}</span>` : ""}
      <span class="cl-meta-chip" title="인스턴스 전체 집계">재발행 ${p.stats.reshares} · 가져감 ${p.stats.imports}</span>
    </div>`;
  }

  function actionButtons(p) {
    const accs = writable();
    const b = (mode) => {
      const m = MODES[mode];
      const allow = modeAllowed(p, mode);
      const disabled = !accs.length || !allow.ok;
      const tip = !accs.length ? "이 테마에는 쓰기 권한이 있는 연결 계정이 없습니다 (읽기 전용)" : (allow.why || "");
      return `<button type="button" class="tt-btn ${mode === "import_publish" ? "tt-btn-pri" : "tt-btn-ap"}" data-peer-act="${mode}" data-peer-id="${esc(p.id)}" ${disabled ? `disabled title="${esc(tip)}"` : ""}>${m.icon} ${esc(m.label)}</button>`;
    };
    const imp = importedItem(p);
    return `<div class="peer-actions" role="group" aria-label="내 계정으로 발행">
      <span class="peer-actions-label">내 계정으로 발행</span>
      ${b("boost")}${b("quote")}${b("link")}${b("import_publish")}
      <span class="cl-act-sep"></span>
      ${imp
        ? `<button type="button" class="tt-btn" data-peer-act="open-kb" data-peer-id="${esc(p.id)}">📚 지식베이스에서 열기</button>`
        : `<button type="button" class="tt-btn" data-peer-act="import" data-peer-id="${esc(p.id)}" ${p.reuse.import && !p.retracted ? "" : `disabled title="${esc(p.retracted ? "철회된 본문" : "원 작성자가 가져오기를 허용하지 않았습니다")}"`}>📥 가져오기만</button>`}
    </div>`;
  }

  function whereHtml(p) {
    const mine = myPostFor(p);
    const rows = p.posts.map((x) => `<li>${protoDot(x.proto)} <b>${esc(x.handle)}</b>
        <a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">원격 서버에서 열기 ↗</a>
        ${mine && mine.uri === x.uri ? `<button type="button" class="tt-linklike" data-peer-act="open-client-post" data-peer-id="${esc(p.id)}">🌐 내 타임라인에서 보기</button>` : ""}
        <code>${esc(x.uri)}${x.cid ? ` · cid ${esc(x.cid)}` : ""}</code></li>`).join("");
    return `<section class="cl-kb-panel" aria-label="발행 위치">
      <div class="cl-kb-title">발행된 위치</div>
      <ul class="peer-where">
        <li>🔗 <b>Claire 공개 공유 페이지</b> <a href="${esc(originUrl(p))}" target="_blank" rel="noopener noreferrer">${esc(originUrl(p))}</a></li>
        ${rows || `<li class="cl-handle">연합우주 게시물 없음 — 부스트 · 네이티브 인용은 할 수 없고 링크 공유만 가능합니다.</li>`}
        ${p.source ? `<li>📄 원 수집 출처 <a href="${esc(p.source.url)}" target="_blank" rel="noopener noreferrer">${esc(p.source.title)}</a></li>` : ""}
      </ul>
    </section>`;
  }

  function myActivityHtml(p) {
    const imp = importedItem(p);
    const log = myLog(p).filter((x) => x.mode !== "import");
    const lines = [
      imp ? `<li>${imp.ingestState === "linked" ? "🔗" : "📥"} 지식베이스로 가져옴 <code>${esc(imp.id)}</code> — ${imp.ingestState === "linked" ? `적재 완료 <code>${esc(imp.linkedDoc.docId)}</code>` : "미적재 (엔티티는 적재 시 생성)"}</li>` : "",
      ...log.map((x) => `<li>${MODES[x.mode].icon} ${esc(MODES[x.mode].label)} · ${protoDot(x.proto)} <b>${esc(x.handle)}</b> · ${esc(x.at)}${x.note ? ` <span class="tt-req-note">${esc(x.note)}</span>` : ""}</li>`)
    ].filter(Boolean);
    return `<section class="cl-kb-panel" aria-label="이 테마에서 내 활동">
      <div class="cl-kb-title">이 테마에서 내 활동</div>
      ${lines.length ? `<ul class="peer-where">${lines.join("")}</ul>` : `<p>아직 재발행하거나 가져오지 않았습니다.</p>`}
    </section>`;
  }

  function retractedHtml(p) {
    if (!p.retracted) return "";
    const imp = importedItem(p);
    return `<div class="tt-summary-callout peer-retracted"><strong>원 작성자가 발행을 철회했습니다 (${esc(p.retractedAt || "방금")}).</strong>
      <ul>
        <li>공용 색인과 공개 공유 페이지에서 내려가, 다른 사용자 목록에는 더 이상 나오지 않습니다.</li>
        <li>연합우주 원 게시물이 삭제되면 내 부스트는 서버가 함께 지웁니다. 내 링크 공유 · 인용 글은 남지만 링크가 끊깁니다.</li>
        ${imp ? `<li>가져온 사본 <code>${esc(imp.id)}</code>은 내 워크스페이스에 남습니다. 새로 발행하거나 인용할 수는 없습니다.</li>` : ""}
      </ul></div>`;
  }

  function readerHtml(userKey) {
    const p = selected(userKey);
    if (!p) return `<div style="padding:36px;color:var(--muted);font-size:13px">다른 사용자가 공개 발행한 본문이 없습니다.</div>`;
    const u = user(p.userSeq);
    const matches = entityMatches(p);
    const fontCls = prefs.readerFont === "serif" ? "font-serif" : "";
    return `<div class="tt-article-card peer-reader" data-peer-reader="${esc(p.id)}">
      <header class="tt-art-header">
        <div class="tt-art-title-row">
          <h2 class="tt-art-title"><a href="${esc(originUrl(p))}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a></h2>
          <a class="tt-btn" href="${esc(originUrl(p))}" target="_blank" rel="noopener noreferrer" title="원 작성자의 Claire 공개 공유 페이지">공유 페이지 ↗</a>
        </div>
        <div class="tt-art-meta">
          <span><span class="cl-avatar is-small peer-avatar" aria-hidden="true">${initial(u.name)}</span> <strong>${esc(u.name)}</strong> (Claire 사용자 #${u.seq}) · 테마 <b>${esc(p.themeLabel)}</b> · 발행 ${esc(p.publishedAt)}</span>
          <span><code title="공용 색인 조회 (워크스페이스 DB 아님)">${esc(DATA.indexApi)}/${esc(p.id)}</code> <span class="peer-proposal">제안</span></span>
        </div>
      </header>
      ${retractedHtml(p)}
      ${reuseChips(p)}
      ${actionButtons(p)}
      <div class="tt-reader-body ${fontCls}" style="--read-fs:${prefs.readerFontSize}px">
        <div class="tt-summary-callout"><strong>📌 발행 요약</strong> — ${esc(p.summary)}</div>
        <article class="doc-content">${p.bodyHtml}</article>
      </div>
      <footer class="tt-entity-bar" aria-label="원 작성자 엔티티">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
          <span style="font-weight:700;color:var(--muted)">원 작성자 엔티티:</span>
          ${p.entities.map((e) => entityChip(e, matches)).join("") || `<span style="color:var(--muted)">없음</span>`}
        </div>
        <span style="font-size:11px;color:var(--muted)">* 다른 사용자 워크스페이스의 엔티티입니다. 가져오면 #수집 태그가 되고, 적재할 때 내 테마 엔티티로 생성됩니다.</span>
      </footer>
      <div class="cl-thread" style="padding-top:12px">
        ${whereHtml(p)}
        ${myActivityHtml(p)}
      </div>
      ${p.retracted ? "" : `<p class="cl-tree-note">프로토타입: <button type="button" class="tt-linklike" data-peer-act="retract-sim" data-peer-id="${esc(p.id)}">원 작성자가 발행을 철회하면?</button></p>`}
    </div>`;
  }

  /* ── 가져오기 (수집 원문으로) ── */
  function ensureImportFeed() {
    let f = theme().feeds.find((x) => x.id === IMPORT_FEED_ID);
    if (!f) {
      f = { id: IMPORT_FEED_ID, folder: "Claire 사용자 본문", proto: "claire", format: "Claire 발행 색인", interval: "수동", title: "가져온 사용자 발행 본문", url: DATA.indexApi.replace(/^GET /, ""), source: "다른 사용자 발행 본문 가져오기", etag: "공용 색인 · 수동" };
      theme().feeds.push(f);
    }
    return f;
  }

  function importToKb(p, ingestNow) {
    let item = importedItem(p);
    if (!item) {
      if (!p.reuse.import || p.retracted) {
        ticker(`🔒 '${p.title}' — ${p.retracted ? "철회된 본문입니다" : "원 작성자가 가져오기를 허용하지 않았습니다"}. 부스트 · 링크 공유는 할 수 있습니다.`);
        return null;
      }
      const u = user(p.userSeq);
      const share = originUrl(p);
      /* 원 작성자의 [[엔티티]] 표기는 내 워크스페이스에서 의미가 없으므로 평문으로 바꾸고, 이름은 #수집 태그로 보존한다. */
      const plainBody = p.bodyHtml.replace(/<code>\[\[([^\]]+)\]\]<\/code>/g, "<b>$1</b>").replace(/\[\[([^\]]+)\]\]/g, "$1");
      item = {
        id: `kb_${p.id}_${Date.now().toString(36)}`, feedId: ensureImportFeed().id, proto: "claire", format: "Claire",
        peerPubId: p.id, peerUserSeq: p.userSeq,
        title: p.title, author: `${u.name} (Claire 사용자 #${u.seq})`, url: share,
        timeAgo: p.publishedAt, timestamp: Math.floor(Date.now() / 1000), unread: true, starred: false,
        score: Math.min(99, 40 + entityMatches(p).length * 18),
        sourceTags: p.entities.map((e) => e.name),
        rawBody: p.summary,
        rawHtml: `${plainBody}<p style="font-size:0.85em;color:var(--muted)">↩ 원문: ${esc(u.name)} 님의 Claire 발행 본문 · <code>${esc(share)}</code>${p.license ? ` · ${esc(p.license)}` : ""}${p.source ? ` · 원 수집 출처 <a href="${esc(p.source.url)}" target="_blank" rel="noopener noreferrer">${esc(p.source.title)}</a>` : ""}</p>`,
        ingestState: "unlinked", linkedDoc: null, publishState: "none", publishedInfo: null
      };
      theme().items.unshift(item);
      p.stats.imports += 1;
      logAct(p, { mode: "import" });
      if (!ingestNow) ticker(`📥 '${p.title}' 을(를) 지식베이스 수집함으로 가져옴 (${item.id}) — 원 작성자 엔티티 ${item.sourceTags.length}개는 #수집 태그로 보존되며, 엔티티는 적재 시 생성됩니다.`);
    }
    if (ingestNow && item.ingestState !== "linked") api.ingestItemToClaire(item.id, "");
    return item;
  }

  /* ── 내 계정으로 발행: 연결 서버 API 요청 구성 ── */
  function attribution(p, acc) {
    const u = user(p.userSeq);
    const same = u.accounts.find((a) => a.proto === acc.proto);
    return same ? `via ${same.handle}` : `${u.name} 님의 Claire 발행 본문`;
  }
  function tagsOf(p) {
    return prefs.tagPolicy.attachEntityHashtagsOnPublish ? p.entities.map((e) => api.hashtagToken(e.name).slice(1)) : [];
  }

  function linkPost(p, acc, comment, tags, note) {
    const hash = tags.map((t) => `#${t}`).join(" ");
    const share = originUrl(p);
    const tail = `\n🔗 ${share}\n${attribution(p, acc)}${hash ? ` ${hash}` : ""}`;
    const budget = Math.max(40, (acc.charLimit || 500) - api.graphemeLength(tail));
    const head = api.truncateGraphemes(`${comment ? `${comment}\n\n` : ""}📌 ${p.title}`, budget);
    const req = api.buildServerRequest(acc, {
      kind: "post",
      text: acc.proto === "at" ? `${head}\n${attribution(p, acc)}${hash ? ` ${hash}` : ""}` : `${head}${tail}`,
      tags,
      external: acc.proto === "at" ? { url: share, title: p.title, description: p.summary } : null
    });
    return { acc, req, note, effMode: "link" };
  }

  function requestFor(p, acc, mode, comment) {
    const same = samePost(p, acc);
    const tags = tagsOf(p);
    if (mode === "boost") {
      if (!same) return linkPost(p, acc, comment, tags, `원 작성자가 ${acc.proto === "ap" ? "ActivityPub" : "AT"} 로 발행하지 않음 → 링크 공유로 대체`);
      const req = api.buildServerRequest(acc, { kind: "boost", target: { proto: acc.proto, remoteId: null, uri: same.uri, cid: same.cid, url: same.url } });
      return { acc, req, note: acc.proto === "ap" ? "원 게시물을 내 서버에서 조회한 뒤 부스트" : "원 레코드 리포스트", effMode: "boost" };
    }
    if (mode === "quote") {
      if (!same) return linkPost(p, acc, comment, tags, "같은 프로토콜의 원 게시물 없음 → 링크 인용");
      if (acc.proto === "ap" && !api.accountSupports(acc, "quote")) return linkPost(p, acc, comment, tags, "연결 서버가 인용 게시물을 제공하지 않음 → 링크 인용");
      const hash = tags.map((t) => `#${t}`).join(" ");
      const text = `${comment ? `${comment}\n\n` : ""}${attribution(p, acc)}${hash ? ` ${hash}` : ""}`;
      const quote = acc.proto === "ap" ? { proto: "ap", remoteId: "{resolved_id}", url: same.url } : { proto: "at", uri: same.uri, cid: same.cid };
      const req = api.buildServerRequest(acc, { kind: "post", text, tags, quote });
      if (acc.proto === "ap") req.note = `선행 조회: GET https://${acc.server}/api/v2/search?q=${encodeURIComponent(same.uri)}&resolve=true&type=statuses → {resolved_id}`;
      return { acc, req, note: acc.proto === "ap" ? "네이티브 인용 (quoted_status_id · 원 게시물의 인용 허용 범위를 따름)" : "네이티브 인용 (app.bsky.embed.record)", effMode: "quote" };
    }
    if (mode === "link") return linkPost(p, acc, comment, tags, "");
    /* import_publish: 가져와 내 테마 DB 에 적재한 뒤 내 적재 본문을 발행한다. 미리보기의 요약 자리는 적재 결과로 바뀐다. */
    const imp = importedItem(p);
    const hash = tags.map((t) => `#${t}`).join(" ");
    const mySummary = imp && imp.linkedDoc ? imp.linkedDoc.summary : "{내 적재 요약}";
    const myShare = imp && imp.linkedDoc && String(imp.linkedDoc.shareUrl || "").startsWith("/") ? `${window.location.origin}${imp.linkedDoc.shareUrl}` : "{내 공유 링크}";
    const tail = `\n🔗 ${myShare}\n↩ 원문: ${attribution(p, acc)} ${originUrl(p)}${hash ? `\n${hash}` : ""}`;
    const budget = Math.max(40, (acc.charLimit || 500) - api.graphemeLength(tail));
    const body = api.truncateGraphemes(`📡 ${p.title}\n${mySummary}`, budget);
    const req = api.buildServerRequest(acc, {
      kind: "post",
      text: acc.proto === "at" ? `${body}\n↩ ${attribution(p, acc)}${hash ? ` ${hash}` : ""}` : `${body}${tail}`,
      tags,
      external: acc.proto === "at" ? { url: myShare, title: p.title, description: mySummary } : null
    });
    const steps = imp ? (imp.ingestState === "linked" ? "적재 본문 발행" : "적재(내 LLM 예산) → 적재 본문 발행") : "가져오기 → 적재(내 LLM 예산) → 적재 본문 발행";
    return { acc, req, note: steps, effMode: "import_publish" };
  }

  function previewHtml(reqs) {
    return reqs.length ? reqs.map((r) => `<div class="tt-req">
        <div class="tt-req-head">${protoDot(r.acc.proto)} <b>${esc(r.acc.handle)}</b>${r.note ? ` <span class="tt-req-note">${esc(r.note)}</span>` : ""}</div>
        <pre>${esc(`${r.req.method} ${r.req.url}`)}${r.req.note ? `\n# ${esc(r.req.note)}` : ""}${r.req.body ? `\n${esc(JSON.stringify(r.req.body, null, 2))}` : ""}</pre>
      </div>`).join("") : `<div class="tt-summary-callout" style="margin:0">발행할 계정을 하나 이상 선택하세요.</div>`;
  }

  function postTextOf(req) {
    if (!req || !req.body) return "";
    if (req.body.status !== undefined) return req.body.status;
    return req.body.record && req.body.record.text ? req.body.record.text : "";
  }

  /* 발행 결과를 연합 클라이언트 타임라인과 활동 기록에 반영한다. */
  function recordReshare(p, reqs) {
    const posts = theme().posts || (theme().posts = []);
    const origin = myPostFor(p);
    reqs.forEach((r) => {
      const acc = r.acc;
      if (r.effMode === "boost") {
        const same = samePost(p, acc);
        const existing = posts.find((x) => x.accountId === acc.id && same && x.uri === same.uri);
        if (existing && !existing.boosted) { existing.boosted = true; existing.counts.boosts += 1; }
      } else {
        const home = acc.timelines.find((t) => t.id === "home" || t.id === "following");
        const rkey = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 5)}`;
        posts.push({
          id: `own_${acc.id}_${rkey}`, accountId: acc.id, own: true, unread: false, timelines: home ? [home.id] : [], visibility: "public", lang: "ko",
          author: acc.displayName || acc.handle, handle: acc.handle, time: "방금", timestamp: Math.floor(Date.now() / 1000),
          text: postTextOf(r.req), hashtags: tagsOf(p),
          card: r.effMode === "quote" ? null : { title: p.title, description: p.summary, url: originUrl(p) },
          quoteOf: r.effMode === "quote" && origin && origin.accountId === acc.id ? origin.id : undefined,
          quoteState: r.effMode === "quote" && origin && origin.accountId === acc.id ? "accepted" : undefined,
          url: `https://${acc.server}`, uri: acc.proto === "ap" ? `https://${acc.server}/statuses/${rkey}` : `at://${acc.did}/app.bsky.feed.post/${rkey}`,
          remoteId: acc.proto === "ap" ? rkey : undefined, cid: acc.proto === "at" ? `bafyrei${rkey}` : undefined,
          counts: { replies: 0, boosts: 0, favs: 0 }, boosted: false, favourited: false, bookmarked: false,
          peerPubId: p.id
        });
      }
      logAct(p, { mode: r.effMode, proto: acc.proto, handle: acc.handle, note: r.note && /대체|링크 인용/.test(r.note) ? r.note : "" });
    });
    p.stats.reshares += reqs.length;
  }

  function openReshare(pubId, presetMode) {
    const p = pub(pubId);
    if (!p) return;
    if (p.retracted) {
      ticker(`🔒 '${p.title}' — 원 작성자가 발행을 철회해 새로 발행할 수 없습니다.`);
      return;
    }
    const accs = writable();
    if (!accs.length) {
      ticker("🔒 이 테마에는 쓰기 권한이 있는 연결 계정이 없습니다 — 읽기 전용 계정으로는 발행할 수 없습니다.");
      return;
    }
    const modal = document.getElementById("modal-reshare");
    const box = document.getElementById("modal-reshare-body");
    if (!modal || !box) return;
    const u = user(p.userSeq);
    const firstOk = ["boost", "quote", "link"].find((m) => modeAllowed(p, m).ok) || "link";
    let mode = presetMode && MODES[presetMode] && modeAllowed(p, presetMode).ok ? presetMode : firstOk;
    const selected = new Set(accs.filter((a) => mode !== "boost" || samePost(p, a)).map((a) => a.id));
    if (!selected.size) accs.forEach((a) => selected.add(a.id));
    let comment = "";

    function chosen() { return accs.filter((a) => selected.has(a.id)); }
    function requests() { return chosen().map((a) => requestFor(p, a, mode, comment)); }

    function draw() {
      const reqs = requests();
      const readonlyAccs = api.themeAccounts().filter((a) => !a.canPublish);
      const imp = importedItem(p);
      const importLabel = imp ? (imp.ingestState === "linked" ? "📡 적재 본문 발행" : "📡 적재 후 발행") : "📡 가져와 적재 발행";
      const seg = (m, label) => {
        const allow = modeAllowed(p, m);
        return `<button type="button" class="tt-seg-btn ${mode === m ? "active" : ""}" data-rs-mode="${m}" ${allow.ok ? "" : `disabled title="${esc(allow.why)}"`}>${label}</button>`;
      };
      box.innerHTML = `<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
          <div style="min-width:0">
            <h2 id="modal-reshare-title" style="margin:0;font-size:16px;font-weight:700">내 계정으로 발행 — 다른 사용자 발행 본문 <span class="peer-proposal">제안</span></h2>
            <p style="margin:4px 0 0;font-size:12px;color:var(--muted)"><b>${esc(u.name)}</b> (Claire 사용자 #${u.seq}) · ${esc(p.title)}${p.license ? ` · ${esc(p.license)}` : ""}</p>
          </div>
          <button type="button" class="tt-btn" data-rs-close="1" aria-label="닫기">✕</button>
        </div>
        ${reuseChips(p)}
        <fieldset class="tt-fieldset">
          <legend>발행할 내 연결 계정 (지금 테마 · 교차 게시 가능)</legend>
          <div class="tt-acc-pick-row">${accs.map((a) => `<label class="tt-acc-pick ${selected.has(a.id) ? "on" : ""}">
              <input type="checkbox" data-rs-acc="${esc(a.id)}" ${selected.has(a.id) ? "checked" : ""}/>
              <span>${protoDot(a.proto)} <b>${esc(a.handle)}</b> <small>${samePost(p, a) ? "원 게시물 있음" : "원 게시물 없음 · 링크로 대체"}</small></span>
            </label>`).join("")}</div>
          ${readonlyAccs.length ? `<div style="font-size:11px;color:var(--muted)">읽기 전용 계정 제외: ${readonlyAccs.map((a) => esc(a.handle)).join(", ")}</div>` : ""}
        </fieldset>
        <div class="tt-seg peer-mode-seg">
          ${seg("boost", "🔁 부스트")}${seg("quote", "❝ 인용 발행")}${seg("link", "✎ 링크 공유")}${seg("import_publish", importLabel)}
        </div>
        ${mode === "quote" || mode === "link" ? `<div><label for="rs-comment" style="display:block;font-size:12px;font-weight:700;margin-bottom:4px">내 코멘트 (선택)</label><input id="rs-comment" type="text" class="tt-search" style="width:100%" value="${esc(comment)}" placeholder="예: 우리 팀 FTS5 색인에도 적용해 볼 만합니다"/></div>` : ""}
        ${mode === "import_publish" ? `<div class="tt-summary-callout" style="margin:0">${imp ? "" : "내 지식베이스 수집함으로 가져온 뒤 "}내 테마 DB 에 적재하고(내 월 LLM 예산 사용), 만들어진 <b>내 적재 본문</b>을 발행합니다. 글에는 원문 링크와 원 작성자 표시가 붙습니다.</div>` : ""}
        <div>
          <div style="font-size:11.5px;font-weight:700;color:var(--muted);margin-bottom:4px">연결 서버 API 요청 미리보기 (Claire 서버 프록시 경유 · 토큰은 서버에만 보관)</div>
          <div id="rs-preview">${previewHtml(reqs)}</div>
        </div>
        <div style="display:flex;justify-content:flex-end;gap:8px;padding-top:10px;border-top:1px solid var(--border)">
          <button type="button" class="tt-btn" data-rs-close="1">취소</button>
          <button type="button" class="tt-btn ${mode === "import_publish" ? "tt-btn-pri" : "tt-btn-ap"}" data-rs-submit="1" ${reqs.length ? "" : "disabled"}>${mode === "import_publish" ? importLabel : `${MODES[mode].icon} ${MODES[mode].label} 실행`}</button>
        </div>`;
      const input = box.querySelector("#rs-comment");
      if (input) input.addEventListener("input", (e) => {
        comment = e.target.value;
        box.querySelector("#rs-preview").innerHTML = previewHtml(requests());
      });
    }

    box.onclick = (e) => {
      const t = e.target;
      if (t.closest("[data-rs-close]")) { modal.close(); return; }
      const m = t.closest("[data-rs-mode]");
      if (m && !m.disabled) { mode = m.getAttribute("data-rs-mode"); draw(); return; }
      const s = t.closest("[data-rs-submit]");
      if (s && !s.disabled) { submit(); }
    };
    box.onchange = (e) => {
      const c = e.target.closest("[data-rs-acc]");
      if (!c) return;
      if (c.checked) selected.add(c.getAttribute("data-rs-acc")); else selected.delete(c.getAttribute("data-rs-acc"));
      draw();
    };

    function submit() {
      const accsNow = chosen();
      if (!accsNow.length) return;
      if (mode === "import_publish") {
        const item = importToKb(p, true);
        if (!item) return;
        item.publishState = "published_create";
        item.publishedInfo = { mode: "ingested_create", at: "방금 전", accounts: accsNow.map((a) => a.id), note: `가져온 본문 적재 발행 (원문: ${u.name})` };
      }
      const reqs = accsNow.map((a) => requestFor(p, a, mode, comment));
      recordReshare(p, reqs);
      p.unread = false;
      modal.close();
      ticker(`${MODES[mode].icon} ${MODES[mode].label} 완료 → ${reqs.map((r) => r.acc.handle).join(", ")} · ${u.name} 님의 '${p.title}'`);
      api.renderAll();
    }

    draw();
    modal.showModal();
  }

  function simulateRetract(p) {
    p.retracted = true;
    p.retractedAt = "방금";
    const imp = importedItem(p);
    ticker(`🧪 ${user(p.userSeq).name} 님이 '${p.title}' 발행을 철회 — ${touched(p) ? "내가 가져오거나 재발행했으므로 '철회됨'으로 남습니다" : "목록에서 사라졌습니다"}${imp ? ` · 가져온 사본 ${imp.id}은 유지` : ""}`);
    api.renderAll();
  }

  /* 지식베이스 리더 상단: 가져온 항목의 출처 표시 */
  function provenanceHtml(item) {
    if (!item || !item.peerPubId) return "";
    const p = pub(item.peerPubId);
    if (!p) return "";
    const u = user(p.userSeq);
    return `<div class="tt-summary-callout peer-provenance ${p.retracted ? "peer-retracted" : ""}">
      <strong>👥 다른 사용자 발행 본문에서 가져옴</strong> — ${esc(u.name)} (Claire 사용자 #${u.seq}) · 테마 ${esc(p.themeLabel)} · <a href="${esc(originUrl(p))}" target="_blank" rel="noopener noreferrer">원문 공유 페이지</a>${p.license ? ` · ${esc(p.license)}` : ""}
      ${p.retracted ? `<div style="margin-top:4px"><b>원 작성자가 발행을 철회했습니다.</b> 이 사본은 내 워크스페이스에 남지만 새로 발행할 수는 없습니다.</div>` : `<div style="margin-top:4px;font-size:11.5px;color:var(--muted)">발행하면 원문 링크와 원 작성자 표시가 붙습니다. <button type="button" class="tt-linklike" data-peer-goto="${esc(p.id)}">원문 보기 →</button></div>`}
    </div>`;
  }

  /* 연합 클라이언트 게시물 머리의 배지: 같은 인스턴스 사용자의 발행 게시물이면 전체 본문으로 이동 */
  function postBadge(post) {
    if (!post) return "";
    const own = post.own && post.peerPubId ? pub(post.peerPubId) : null;
    const p = own || pubForUri(post.uri);
    if (!p) return "";
    const name = esc(user(p.userSeq).name);
    return own
      ? `<button type="button" class="tt-link-dot peer-dot" data-peer-goto="${esc(p.id)}" title="${name} 님의 Claire 발행 본문을 내 계정으로 재발행한 글 — 원문 보기">👥 원문 · ${name}</button>`
      : `<button type="button" class="tt-link-dot peer-dot" data-peer-goto="${esc(p.id)}" title="같은 Claire 인스턴스의 ${name} 님이 발행한 본문 — 전체 본문 보기 · 내 계정으로 발행">👥 Claire 본문</button>`;
  }

  function keyTarget(userKey) {
    const vis = visible(userKey);
    const idx = vis.findIndex((x) => x.id === ps.selectedId);
    return { vis, idx, cur: vis[idx >= 0 ? idx : 0] };
  }

  function handleKey(e, userKey) {
    const { vis, idx, cur } = keyTarget(userKey);
    if (!vis.length) return false;
    if (e.key === "j" || e.key === "k") {
      const next = vis[e.key === "j" ? Math.min(vis.length - 1, idx + 1) : Math.max(0, idx - 1)] || vis[0];
      openPub(next.id);
    } else if (e.key === "p" || e.key === "b") {
      openReshare(cur.id, e.key === "b" ? "boost" : undefined);
    } else if (e.key === "s") {
      importToKb(cur, false);
      api.renderAll();
    } else if (e.key === "i") {
      importToKb(cur, true);
    } else {
      return false;
    }
    e.preventDefault();
    return true;
  }

  function openPub(id) {
    ps.selectedId = id;
    const p = pub(id);
    if (p && prefs.autoMarkReadOnOpen) p.unread = false;
    api.renderAll();
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    let x;
    if ((x = t.closest("[data-peer-view]"))) {
      const tab = x.getAttribute("data-peer-view");
      ps.clientUser = x.getAttribute("data-peer-user") || "all";
      ps.selectedId = null;
      api.showPeerView(tab === "current" ? state.appTab : tab, ps.clientUser);
      return;
    }
    if ((x = t.closest("[data-peer-goto]"))) {
      e.stopPropagation();
      ps.selectedId = x.getAttribute("data-peer-goto");
      ps.clientUser = "all";
      const p = pub(ps.selectedId);
      if (p) p.unread = false;
      api.showPeerView(state.appTab, "all");
      return;
    }
    if ((x = t.closest("[data-peer-act]"))) {
      if (x.disabled) return;
      const p = pub(x.getAttribute("data-peer-id"));
      if (!p) return;
      const act = x.getAttribute("data-peer-act");
      if (act === "reshare") openReshare(p.id);
      else if (MODES[act]) openReshare(p.id, act);
      else if (act === "import") { importToKb(p, false); api.renderAll(); }
      else if (act === "open-kb") { const it = importedItem(p); if (it) api.openKbItem(it.id); }
      else if (act === "open-client-post") { const post = myPostFor(p); if (post) api.openClientPost(post.id); }
      else if (act === "retract-sim") simulateRetract(p);
      return;
    }
    if ((x = t.closest("[data-peer-open]")) && !t.closest("button, a, input, label, select, textarea")) openPub(x.getAttribute("data-peer-open"));
  });

  return {
    VIEW_LABEL, treeHtml, streamHeadHtml, streamHtml, readerHtml, handleKey, openReshare, provenanceHtml, postBadge,
    unreadCount, visibleCount: (k) => visible(k).length, clientUser: () => ps.clientUser,
    footerText: (k) => `공용 발행 색인 · 다른 사용자 ${peerUsers().length}명 · 발행 본문 ${visible(k).length}건 · 이 테마로 가져옴 ${theme().items.filter((it) => it.peerPubId).length}건`
  };
};
