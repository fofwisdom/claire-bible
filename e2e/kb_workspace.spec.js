const { test, expect } = require('@playwright/test');
const { authenticateOwner, json, watchErrors, expectNoRawKeys, kbInfo, kbItem, installJsonApi } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// The workspace of a shared knowledge base ("/kb/101/?doc=…": the shared knowledge base bar of app.js with the buttons of kb_dialogs.js), and the campaign notices of the ingest dialog (ingest_dialogs.js, design Section 8.7). The membership, the contribution, the fork, the ingest stream, and the submission get fixed answers in the shapes of api/server.py ("_shared_kb_root", "kb_membership_route", "kb_contribute_route", "kb_forks_route", "campaign.notices_for_ingest", "_submission_view").
// The E2E server runs in single mode without CLAIRE_MULTI_THEME: "_select_kb" gives 404 for each knowledge base other than 0, also for "/kb/101/". Thus the spec gives the workspace page of the admin ("/@e2eadmin/?doc=doc-1", from the server) with the API base "/kb/101/", and sends each content request of "/kb/101/…" (graph, documents, statistics) to "/@e2eadmin/…" of the server.

const ADMIN_BASE = '/@e2eadmin/';

async function installPinnedWorkspace(page) {
  await page.route((u) => u.pathname.startsWith('/kb/101/'), async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const rest = url.pathname.slice('/kb/101/'.length);
    if (req.resourceType() === 'document') {
      const res = await route.fetch({ url: new URL(ADMIN_BASE + rest + url.search, url).toString() });
      const html = (await res.text()).replace(/(<meta name="claire-api-base" content=")[^"]*(")/, '$1/kb/101/$2');
      expect(html).toContain('<meta name="claire-api-base" content="/kb/101/"');
      return route.fulfill({ status: res.status(), contentType: 'text/html; charset=utf-8', body: html });
    }
    return route.fallback({ url: new URL(ADMIN_BASE + rest + url.search, url).toString() });
  });
}

async function installBarApi(page, kb, opts) {
  const options = opts || {};
  // The route of the workspace comes first: Playwright calls the last route first, and "fallback" of the bar routes goes to it.
  await installPinnedWorkspace(page);
  return installJsonApi(page, (u) => /^\/kb\/101\/(membership|forks|contributions)?$/.test(u.pathname), ({ path, method, body }) => {
    // GET /kb/101/ with "Accept: application/json" (the bar); a navigation does not come here.
    if (path === '/kb/101/' && method === 'GET') return [{ knowledge_base: kb }];
    if (path === '/kb/101/membership' && method === 'POST') {
      // "kb_join_route": a banned account has no capability "join" (kb_access._BANNED), and "_require_kb" gives 403 "kb_capability_required".
      if (options.banned) return [{ error: 'kb_capability_required' }, 403];
      if (kb.join_policy === 'open') Object.assign(kb, { role: 'member', membership_state: 'active' });
      else Object.assign(kb, { role: null, membership_state: 'pending' });
      // The membership in the shape of "_member_view" (single mode: no username).
      return [{ ok: true, membership: { number: '101002', account: '1000001', display_name: 'E2E Admin', role: 'member', state: kb.membership_state, requested_at: 1700000000, decided_at: kb.membership_state === 'active' ? 1700000000 : null } }, 201];
    }
    if (path === '/kb/101/membership' && method === 'DELETE') {
      Object.assign(kb, { role: null, membership_state: null });
      return [{ ok: true }];
    }
    if (path === '/kb/101/contributions' && method === 'POST') return [{ ok: true, contribution: { number: '9100', document: 'copy-' + body.document, title: null, origin: 'direct', state: 'pending', contributor: null, approved: false, approved_at: null, updates_stopped: false, contributed_at: 1700000000, revised_at: null, remote: null, origin_removed: null, reason: '', revisions: [], offer: null } }, 201];
    if (path === '/kb/101/forks' && method === 'POST') {
      if (options.forkAnswers && options.forkAnswers.length) return options.forkAnswers.shift();
      // "kb_forks_route": the new knowledge base is "KnowledgeBaseInfo.to_dict()" (not the collection item), with the counts of "contributions.fork_copy".
      return [{ ok: true, knowledge_base: kbInfo(105, { label: 'Modern History (fork)' }), forked_from: '101', documents: 3, entities: 9, relations: 4 }, 201];
    }
    // Each other path of the knowledge base goes to the workspace route.
    return 'fallback';
  });
}

test('the shared knowledge base bar of the workspace: join request, withdraw, join, direct contribution, leave, and their requests', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbwspace1');
  const kb = kbItem(101, { label: 'Modern History', role: null, membership_state: null, join_policy: 'approval' });
  const { calls } = await installBarApi(page, kb);
  await page.goto('/kb/101/?doc=doc-1', { waitUntil: 'domcontentloaded' });
  const bar = page.locator('#shared-kb-bar');
  await expect(bar).toBeVisible();
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#pop-hamburger-menu')).toBeVisible();
  await expect(page.locator('#purgebtn')).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.locator('#pop-hamburger-menu')).toBeHidden();
  // No membership, the join policy "approval": the join request, the fork, and (for the admin) the link to the review page.
  await expect(bar.locator('.shared-kb-bar-head .claire-tag').first()).toHaveText('공유 지식 베이스');
  await expect(bar.locator('[data-kb-act]')).toHaveCount(2);
  await expect(bar.locator('[data-kb-act="join"]')).toHaveText('가입 요청');
  await expect(bar.locator('[data-kb-act="fork"]')).toContainText('포크');
  await expect(bar.locator('a[href="/moderation/kb/101"]')).toHaveText('심사와 관리');
  // Each control of the bar is inside the screen and can get a click.
  for (const el of await bar.locator('[data-kb-act], a.claire-btn').all()) {
    const box = await el.boundingBox();
    expect(box).not.toBeNull();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(1360);
    await el.click({ trial: true });
  }
  await expectNoRawKeys(page, '#shared-kb-bar');

  // Join request: POST with an empty object; the bar shows the state, the note, and the control that withdraws the request.
  await bar.locator('[data-kb-act="join"]').click();
  await expect(bar.locator('[data-kb-act="leave"]')).toHaveText('가입 요청 철회');
  expect(calls.find((c) => c.method === 'POST' && c.path === '/kb/101/membership').body).toEqual({});
  await expect(bar.locator('.shared-kb-bar-head .claire-tag.warn')).toHaveText('가입 요청');
  await expect(bar.locator('.claire-note')).toContainText('모더레이터의 승인을 기다리고 있습니다');
  await expect(bar.locator('[data-kb-act="join"], [data-kb-act="direct"]')).toHaveCount(0);

  // Withdraw: DELETE; the join control is back. The join policy is now "open": the control is "join".
  kb.join_policy = 'open';
  await bar.locator('[data-kb-act="leave"]').click();
  await expect(bar.locator('[data-kb-act="join"]')).toHaveText('가입');
  expect(calls.filter((c) => c.method === 'DELETE' && c.path === '/kb/101/membership')).toHaveLength(1);
  await expect(bar.locator('.claire-note')).toHaveCount(0);

  // Join: the role shows, with the direct contribution and the leave control.
  await bar.locator('[data-kb-act="join"]').click();
  await expect(bar.locator('.shared-kb-bar-head .claire-tag.ok')).toHaveText('구성원');
  await expect(bar.locator('[data-kb-act="direct"]')).toContainText('직접 기여');
  await expect(bar.locator('[data-kb-act="leave"]')).toHaveText('탈퇴');
  await expect(bar.locator('[data-kb-act="fork"]')).toBeVisible();
  await expectNoRawKeys(page, '#shared-kb-bar');

  // Direct contribution: the dialog has the documents of the personal knowledge base (from the server); POST has the document.
  await bar.locator('[data-kb-act="direct"]').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg.locator('h2')).toContainText('이 공유 지식 베이스에 직접 기여');
  await dlg.locator('#kb-doc-search').fill('핵심 개념');
  await expect(dlg.locator('input[name="doc"]')).toHaveCount(1);
  await expect(dlg.locator('label.claire-srow')).toContainText('테스트 문서 1 (핵심 개념)');
  await expect(dlg.locator('[data-doc-count]')).toHaveText('문서 1개 중 1개를 표시합니다.');
  await expectNoRawKeys(page);
  await dlg.locator('input[name="doc"][value="doc-1"]').check();
  await dlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect(dlg).toHaveCount(0);
  expect(calls.find((c) => c.method === 'POST' && c.path === '/kb/101/contributions').body).toEqual({ document: 'doc-1' });
  await expect(page.locator('.claire-toast').last()).toContainText('기여를 보냈습니다.');

  // Leave: DELETE; the bar is that of an account without a membership again.
  await bar.locator('[data-kb-act="leave"]').click();
  await expect(bar.locator('[data-kb-act="join"]')).toBeVisible();
  expect(calls.filter((c) => c.method === 'DELETE' && c.path === '/kb/101/membership')).toHaveLength(2);
  await expect(bar.locator('[data-kb-act="direct"]')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('the shared knowledge base bar: a refused join shows a text, an archived knowledge base has no join and no contribution, and a fork opens the new knowledge base', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbwspace2');
  const kb = kbItem(101, { label: 'Modern History', role: null, membership_state: null, join_policy: 'open' });
  const { calls } = await installBarApi(page, kb, { banned: true, forkAnswers: [[{ error: 'fork_failed' }, 500]] });
  // The page of the fork: a small fixed page, so that the test sees the navigation.
  await page.route((u) => u.pathname === '/kb/105/', (route) => (route.request().resourceType() === 'document' ? route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><title>fork</title><p id="fork-page">fork</p>' }) : route.fallback()));
  await page.goto('/kb/101/?doc=doc-1', { waitUntil: 'domcontentloaded' });
  const bar = page.locator('#shared-kb-bar');
  await expect(bar.locator('[data-kb-act="join"]')).toHaveText('가입');
  // The server refuses the join: the text of the code shows, and the control is ready again.
  await bar.locator('[data-kb-act="join"]').click();
  await expect(page.locator('.claire-toast').last()).toBeVisible();
  await expect(page.locator('.claire-toast').last()).toContainText('이 공유 지식 베이스에서 이 작업을 할 권한이 없습니다');
  await expect(bar.locator('[data-kb-act="join"]')).toBeEnabled();
  await expectNoRawKeys(page);

  // Fork: the dialog names the knowledge base. A failure shows as text in the dialog; the second attempt goes to the new knowledge base.
  await bar.locator('[data-kb-act="fork"]').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg.locator('h2')).toContainText('이 공유 지식 베이스 포크');
  await expect(dlg).toContainText('Modern History');
  await dlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect(dlg.locator('#claire-fork-err')).toHaveText('포크를 끝내지 못했습니다. 지식 베이스를 만들지 않았습니다.');
  await expect(dlg.locator('.claire-dlg-foot .claire-btn.pri')).toBeEnabled();
  await expectNoRawKeys(page);
  await dlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect(page.locator('#fork-page')).toBeVisible();
  expect(new URL(page.url()).pathname).toBe('/kb/105/');
  expect(calls.filter((c) => c.method === 'POST' && c.path === '/kb/101/forks').map((c) => c.body)).toEqual([{}, {}]);

  // An archived knowledge base where the account is a member: no direct contribution; leave and fork stay. A banned account has no join control.
  Object.assign(kb, { state: 'archived', role: 'member', membership_state: 'active' });
  await page.goto('/kb/101/?doc=doc-1', { waitUntil: 'domcontentloaded' });
  await expect(bar.locator('[data-kb-act="leave"]')).toBeVisible();
  await expect(bar.locator('[data-kb-act="direct"], [data-kb-act="join"]')).toHaveCount(0);
  Object.assign(kb, { state: 'active', role: null, membership_state: 'banned' });
  await page.goto('/kb/101/?doc=doc-1', { waitUntil: 'domcontentloaded' });
  await expect(bar.locator('.claire-note.danger')).toContainText('차단했습니다');
  await expect(bar.locator('.shared-kb-bar-head .claire-tag.danger')).toHaveText('차단됨');
  await expect(bar.locator('[data-kb-act="join"], [data-kb-act="direct"], [data-kb-act="leave"]')).toHaveCount(0);
  await expectNoRawKeys(page, '#shared-kb-bar');
  expect(errors).toEqual([]);
});

test('the campaign notices of the ingest dialog: the standing message with its two links, and the notice of a fiction-like document with the action that contributes to the theme', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbwspace3');
  const submissions = [];
  const answers = [[{ error: 'pending_limit' }, 429]];
  // The ingest stream: lines of JSON; the last line has the result with the notices of campaign.notices_for_ingest.
  await page.route((u) => u.pathname.endsWith('/ingest-stream') || u.pathname === '/ingest-stream', (route) => route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: [
    JSON.stringify({ stage: 'fetch', msg: 'reading' }),
    JSON.stringify({ done: true, result: { document_id: 'doc-2', title: 'Space Opera Draft', entities_created: 2, entities_linked: 1, relations_added: 1, notices: [
      { code: 'fiction_like_document', document: 'doc-2', theme: '202', theme_label: 'Dragons · Elves', message: '이 문서는 허구로 보입니다(테마: Dragons · Elves). 이 테마에 기여하면 평판이 유지됩니다.' },
      { code: 'fiction_standing_message', message: "허구는 지식 유형이 '허구'인 공유 지식 베이스에 넣습니다." }
    ] } })
  ].join('\n') + '\n' }));
  await page.route((u) => u.pathname === '/submissions', (route) => {
    const req = route.request();
    if (req.method() !== 'POST') return route.fallback();
    submissions.push(JSON.parse(req.postData()));
    const answer = answers.shift();
    if (answer) return json(route, answer[0], answer[1]);
    return json(route, { ok: true, submission: { number: '7001', created_at: 1700000000, items: [{ document: 'doc-2', title: 'Space Opera Draft', theme: '202', theme_label: 'Dragons · Elves', routed_by: 'account', score: null, targets: [{ knowledge_base: '102', label: 'Fantasy Lore', state: 'pending', contribution: '9200', approved: false }, { knowledge_base: '106', label: 'Elf Atlas', state: 'skipped', reason: 'contribution_duplicate' }] }] } }, 201);
  });

  await page.locator('#addbtn').click();
  await page.locator('#pop-ingest-menu [data-ingest-cmd="ingest"]').click();
  const modal = page.locator('#modal-ingest');
  await expect(modal).toBeVisible();
  // The standing message: its text, the link to the fictional themes, and the control that makes a shared knowledge base. The two are inside the dialog and can get a click.
  const notice = modal.locator('#ingest-fiction-notice');
  await expect(notice).toContainText('소설, 만화, 게임, 영화, 가상 세계 같은 허구는');
  const themesLink = notice.locator('a[href="/themes?knowledge_type=fictional"]');
  await expect(themesLink).toHaveText("지식 유형이 '허구'인 테마 보기");
  const makeKb = notice.locator('#btn-ingest-create-kb');
  await expect(makeKb).toHaveText('공유 지식 베이스 만들기');
  const modalBox = await modal.boundingBox();
  for (const el of [themesLink, makeKb]) {
    const box = await el.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(modalBox.x);
    expect(box.x + box.width).toBeLessThanOrEqual(modalBox.x + modalBox.width + 1);
    await el.click({ trial: true });
  }
  await expectNoRawKeys(page);

  // An ingest whose result has the notice "fiction_like_document": the notice names the theme and has the action.
  await modal.locator('#ingin').fill('A story about dragons and elves.');
  await modal.locator('#btn-submit-ingest').click();
  const result = page.locator('.campaign-notice');
  await expect(result).toHaveCount(1);
  await expect(result).toContainText('이 문서는 허구로 보입니다(테마: Dragons · Elves)');
  const action = result.locator('[data-campaign-contribute="doc-2"]');
  await expect(action).toHaveText('이 테마에 기여');
  await expect(action).toHaveAttribute('data-campaign-theme', '202');
  await expectNoRawKeys(page);

  // The server refuses: the text of the code shows, and the action is ready again.
  await action.click();
  await expect(result.locator('.campaign-notice-msg')).toHaveText('이 공유 지식 베이스에서 심사를 기다리는 기여가 한도에 이르렀습니다.');
  await expect(action).toBeEnabled();
  // The action sends POST /submissions with the document and the theme; the message counts the targets that got the contribution.
  await action.click();
  await expect(result.locator('.campaign-notice-msg')).toHaveText('공유 지식 베이스 1곳에 기여했습니다.');
  await expect(action).toBeHidden();
  expect(submissions).toEqual([{ documents: ['doc-2'], theme: '202' }, { documents: ['doc-2'], theme: '202' }]);
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('the document list of the profile page has no contribution functions: no row menu and no contribution badge, also in multi-theme mode', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbwspace4');
  // Owner decision 2026-11-19: contribution is a function of the syndication page and the console, not of the profile page. The old list showed the row menu "contribute" and "never contribute" only in multi-theme mode, thus the page gets themeMode "multi". The list answer keeps the two old fields, so that only the page decides.
  await page.route((u) => u.pathname === ADMIN_BASE, async (route) => {
    if (route.request().resourceType() !== 'document') return route.fallback();
    const res = await route.fetch();
    const html = (await res.text()).replace(/themeMode: '[^']*'/, "themeMode: 'multi'");
    expect(html).toContain("themeMode: 'multi'");
    return route.fulfill({ status: res.status(), contentType: 'text/html; charset=utf-8', body: html });
  });
  const doc = (id, title, extra) => Object.assign({ id, title, url: null, source_type: 'web', fetched_at: 1700000000, seen: 1, watch: 0, pinned: 0, hidden: 0, summary: '', has_presentation: false, has_detail: false, detail_format: 'adoc' }, extra);
  await page.route((u) => u.pathname === ADMIN_BASE + 'documents', (route) => json(route, { documents: [
    doc('doc-a', 'Agent Substrate: Architecture', { theme_match: { theme: '202', theme_label: 'Dragons · Elves', knowledge_type: 'fictional', score: 0.9 }, never_contribute: false }),
    doc('doc-b', 'Minisforum ESP4B', { theme_match: null, never_contribute: true })
  ], format_status: null, kb_id: 0, theme_label: '', next_before: null, total: 2 }));
  await page.goto(ADMIN_BASE, { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.__CLAIRE_CONFIG.themeMode)).toBe('multi');
  const items = page.locator('#doclist .docitem');
  await expect(items.filter({ hasText: 'Agent Substrate: Architecture' })).toHaveCount(1);
  await expect(items.filter({ hasText: 'Minisforum ESP4B' })).toHaveCount(1);
  await expect(page.locator('.docitem .docitem-actions, .docitem .doc-kb-meta, [data-menu-action^="doc_kb_"]')).toHaveCount(0);
  await expect(page.locator('#doclist')).not.toContainText('기여');
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});
