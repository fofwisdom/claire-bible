const { test, expect } = require('@playwright/test');
const { authenticateOwner, json, watchErrors, expectNoRawKeys, recordCall } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /admin/federation (knowledge federation design Section 6.5) against answers in the shapes of the handlers: "known_server_view" (accounts_routes/knowledge_federation.py), "entry_view" (knowledge_federation/journal.py), "domain_block_view" (accounts_routes/console_federation_panels.py), and the automatic contribution switch (api/server.py). The page itself and GET /api/admin/federation come from the E2E server.

function knownServer(domain, over) {
  return { domain, status: 'active', blocked_by: null, source: 'admin', created_at: 1700000000, last_contact_at: null, server_actor: null, software_name: null, software_version: null, kb_count: null, last_read_at: null, error_count: 0, last_error: null, last_error_at: null, kbs: [], kb_read: null, ...over };
}

function journalEntry(id, status, over) {
  return { id, activity_iri: 'https://b.example/activities/' + id, activity_type: 'Offer', kind: 'offer_contribution', actor: 'https://b.example/ap/actors/7', actor_domain: 'b.example', recipient: 'http://127.0.0.1:8766/ap/kb/101', verified_by: 'http-signature', status, error_code: null, error_detail: null, attempts: 1, received_at: '2026-11-17T10:00:00Z', processed_at: status === 'processed' ? '2026-11-17T10:00:01Z' : null, replayable: status === 'failed' || status === 'received', ...over };
}

async function installApi(page, opts) {
  const options = opts || {};
  const calls = [];
  const state = {
    servers: [
      knownServer('b.example', { source: 'local_follow', last_contact_at: 1700000500, server_actor: 'https://b.example/ap/actors/server', software_name: 'claire', software_version: '0.9.1', kb_count: 1, last_read_at: 1700000600, error_count: 1, last_error: 'nodeinfo_unavailable', last_error_at: 1700000700, kbs: [{ id: 5, iri: 'https://b.example/ap/kb/901', server: 'b.example', url: 'https://b.example/kb/901/', label: 'Railway Archive', description: 'Rail records', knowledge_type: 'real', documents: 12, entities: 40, relations: 22, first_seen_at: 1700000600 }], kb_read: { last_read_at: 1700000650, stored: 1, refused: 2, refused_code: 'record_too_large', error_count: 2, last_error: 'read_timeout', last_error_at: 1700000660 } }),
      knownServer('c.example', { status: 'blocked', blocked_by: 'c.example', source: 'remote_contribution' })
    ],
    // The server renders the first rows of the domain blocks from its database, which has none. The list of the fixed data starts with the same state when a test asks for it.
    blocks: options.noBlocks ? [] : [{ domain: 'c.example', note: 'spam', created_at: 1700000100 }],
    autoState: 'on',
    // The number of change requests that get 401 "reauth_required" before POST /api/auth/reauth gives 200.
    reauthNeeded: !!options.reauth,
    reauthAnswers: (options.reauthAnswers || []).slice(),
    goneDomains: options.goneDomains || []
  };
  // The codes are those of the handlers: "handler_error" (journal.process), "blocked_domain" (access.exchange_refusal at a replay), "revision_too_soon" (contributions.remote_revision_refusal), "follower_limit" (handlers.follow). A refused row has no kind when the contract check refused it, else the kind of the activity.
  const journalRows = [
    journalEntry(40, 'failed', { error_code: 'handler_error', error_detail: 'KeyError: document' }),
    journalEntry(39, 'processed'),
    journalEntry(38, 'received', { attempts: 0, activity_type: 'Follow', kind: null, actor: 'https://b.example/ap/actors/server', recipient: null }),
    journalEntry(37, 'refused', { error_code: 'blocked_domain', actor: 'https://c.example/ap/actors/3', actor_domain: 'c.example' }),
    journalEntry(36, 'refused', { error_code: 'revision_too_soon', activity_type: 'Update', kind: 'update_document', actor: 'https://b.example/ap/kb/901', recipient: null }),
    journalEntry(35, 'refused', { error_code: 'follower_limit', activity_type: 'Follow', kind: 'follow', actor: 'https://d.example/ap/actors/server', actor_domain: 'd.example' })
  ];
  const olderRows = [journalEntry(12, 'processed', { activity_type: 'Update', kind: 'update_document', actor: 'https://b.example/ap/kb/901', recipient: null })];

  await page.route((u) => u.pathname.startsWith('/api/admin/federation/') || u.pathname === '/api/admin/auto-contribution' || u.pathname === '/api/auth/reauth', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    // The reading settings of the server are not a subject of this spec.
    if (path === '/api/admin/federation/reading') return route.fallback();
    recordCall(calls, req);
    if (path === '/api/auth/reauth') {
      const answer = state.reauthAnswers.shift();
      if (answer) return json(route, { error: answer.error }, answer.status);
      state.reauthNeeded = false;
      return json(route, { ok: true, authenticated_at: 1700001000 });
    }
    if (method !== 'GET' && path !== '/api/admin/auto-contribution' && state.reauthNeeded) return json(route, { error: 'reauth_required', reauth: '/api/auth/reauth' }, 401);
    if (path === '/api/admin/federation/known-servers' && method === 'GET') return json(route, { servers: state.servers, switches: { federation: 'off', kb_auto_contribution: state.autoState, signed_get: 'required' } });
    if (path === '/api/admin/federation/known-servers' && method === 'POST') {
      const domain = JSON.parse(req.postData()).domain;
      if (domain === '127.0.0.1') return json(route, { error: 'own_domain' }, 400);
      const known = state.servers.find((s) => s.domain === domain);
      if (known) return json(route, { server: known, created: false });
      const made = knownServer(domain);
      state.servers.push(made);
      return json(route, { server: made, created: true }, 201);
    }
    if (path === '/api/admin/federation/domain-blocks' && method === 'GET') return json(route, { domain_blocks: state.blocks });
    if (path === '/api/admin/federation/domain-blocks' && method === 'POST') {
      const body = JSON.parse(req.postData());
      if (body.domain === 'bad domain') return json(route, { error: 'invalid_domain' }, 400);
      const old = state.blocks.find((b) => b.domain === body.domain);
      if (old) return json(route, { domain_block: old, created: false });
      const block = { domain: body.domain, note: body.note || '', created_at: 1700002000 };
      state.blocks.push(block);
      state.servers = state.servers.map((s) => (s.domain === body.domain ? { ...s, status: 'blocked', blocked_by: body.domain, kbs: [] } : s));
      return json(route, { domain_block: block, created: true }, 201);
    }
    if (path === '/api/admin/federation/domain-blocks' && method === 'DELETE') {
      const domain = JSON.parse(req.postData()).domain;
      if (!state.blocks.some((b) => b.domain === domain)) return json(route, { error: 'domain_block_not_found' }, 404);
      state.blocks = state.blocks.filter((b) => b.domain !== domain);
      state.servers = state.servers.map((s) => (s.domain === domain ? { ...s, status: 'active', blocked_by: null } : s));
      return json(route, { removed: true, domain });
    }
    // DELETE /api/admin/federation/known-servers (accounts_routes/knowledge_federation.py, "known_servers_delete"): the counts of "remove_known_server". A domain of "goneDomains" was removed by another request: 404.
    if (path === '/api/admin/federation/known-servers' && method === 'DELETE') {
      const domain = JSON.parse(req.postData()).domain;
      const known = state.servers.find((s) => s.domain === domain);
      state.servers = state.servers.filter((s) => s.domain !== domain);
      if (!known || state.goneDomains.includes(domain)) return json(route, { error: 'known_server_not_found' }, 404);
      return json(route, { removed: true, domain, deleted: { knowledge_bases: known.kbs.length, listed_documents: 12, followers: 2, outbox_tasks: 3, journal_payloads: 4 } });
    }
    if (path === '/api/admin/federation/journal' && method === 'GET') {
      const status = url.searchParams.get('status');
      if (url.searchParams.get('max_id')) return json(route, { entries: olderRows.filter((e) => !status || e.status === status), next_max_id: null });
      const entries = journalRows.filter((e) => !status || e.status === status);
      // A full page has the cursor of the next page. Only the list without a filter is "full" here.
      return json(route, { entries, next_max_id: status ? null : 35 });
    }
    const replay = /^\/api\/admin\/federation\/journal\/(\d+)\/replay$/.exec(path);
    if (replay && method === 'POST') {
      if (replay[1] === '38') return json(route, { error: 'federation_off' }, 409);
      return json(route, { entry: journalEntry(Number(replay[1]), 'processed', { attempts: 2 }) });
    }
    if (path === '/api/admin/auto-contribution' && method === 'GET') return json(route, { ok: true, state: state.autoState, settings: { auto_match_threshold: 0.5, auto_run_documents: 50 } });
    if (path === '/api/admin/auto-contribution' && method === 'PUT') {
      state.autoState = JSON.parse(req.postData()).state;
      return json(route, { ok: true, state: state.autoState, settings: { auto_match_threshold: 0.5, auto_run_documents: 50 } });
    }
    return json(route, { error: 'not found' }, 404);
  });
  return { calls, state };
}

function tab(page, id) {
  return page.locator('#admin-federation-tabs [role="tab"][data-tab="' + id + '"]');
}

function panel(page, id) {
  return page.locator('#admin-federation-tabs [role="tabpanel"][data-tabpanel="' + id + '"]');
}

test('/admin/federation: the four tabs, the known servers with the detail fields, add a server, block a server with the confirmation', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'fedadmin1');
  const { calls } = await installApi(page);
  await page.goto('/admin/federation', { waitUntil: 'domcontentloaded' });

  // The tabs: switches and state, known servers, domain blocks, inbox journal. One panel shows at a time.
  const tabs = page.locator('#admin-federation-tabs [role="tab"]');
  await expect(tabs).toHaveCount(4);
  await expect(tabs).toHaveText([/스위치|상태/, /알려진 서버/, /차단/, /저널/]);
  await expect(panel(page, 'sw')).toBeVisible();
  await expect(panel(page, 'known')).toBeHidden();
  await expectNoRawKeys(page);

  await tab(page, 'known').click();
  await expect(tab(page, 'known')).toHaveAttribute('aria-selected', 'true');
  await expect(panel(page, 'sw')).toBeHidden();
  const known = page.locator('#admin-federation-known-host');
  await expect(known).toBeVisible();

  // The list: the domain, the state, the software, and the number of shared knowledge bases.
  const items = known.locator('[data-pick-known]');
  await expect(items).toHaveCount(2);
  await expect(items.nth(0)).toContainText('b.example');
  await expect(items.nth(0)).toContainText('활성');
  await expect(items.nth(0)).toContainText('claire 0.9.1');
  await expect(items.nth(1)).toContainText('c.example');
  await expect(items.nth(1)).toContainText('차단됨');
  await expect(items.nth(1)).toContainText('읽기 전');

  // The detail of the first server: each field with its label and its value.
  const field = (name) => known.locator('[data-known-field="' + name + '"]');
  await expect(known.locator('[data-known-read] dt')).toHaveText(['소프트웨어', '버전', '알려진 경로', '알려진 시각', '마지막 접촉', '공유 지식 베이스 수', '서버 마지막 읽기', '공유 지식 베이스 마지막 읽기', '읽기 오류 수']);
  await expect(field('software')).toHaveText('claire');
  await expect(field('version')).toHaveText('0.9.1');
  await expect(field('source')).toHaveText('이 서버의 계정이 그 서버의 계정을 팔로우함');
  await expect(field('created')).toContainText('2023');
  await expect(field('last_contact')).toContainText('2023');
  await expect(field('kb_count')).toHaveText('1');
  await expect(field('kb_read')).toContainText('저장 1개, 거부 2개');
  await expect(field('errors')).toHaveText('3');
  const notes = known.locator('[data-known-error]');
  await expect(notes).toHaveCount(3);
  // Each error shows the catalog text of its code; the code is only in "data-known-code".
  await expect(notes.nth(0)).toContainText('그 서버가 NodeInfo 문서를 주지 않았습니다.');
  await expect(notes.nth(0)).toHaveAttribute('data-known-code', 'nodeinfo_unavailable');
  await expect(notes.nth(1)).toContainText('다른 서버에서 읽는 데 시간이 너무 오래 걸렸습니다.');
  await expect(notes.nth(1)).toHaveAttribute('data-known-code', 'read_timeout');
  await expect(notes.nth(2)).toContainText('2개');
  await expect(notes.nth(2)).toContainText('지식 베이스 기록이 128 KiB보다 큽니다.');
  await expect(notes.nth(2)).toHaveAttribute('data-known-code', 'record_too_large');
  for (const code of ['nodeinfo_unavailable', 'read_timeout', 'record_too_large']) await expect(known.locator('.claire-detail-col')).not.toContainText(code);
  // The shared knowledge bases of the server: the name is the link to the page on that server.
  const remoteKb = known.locator('a[href="https://b.example/kb/901/"]');
  await expect(remoteKb).toHaveText('Railway Archive');
  await expect(remoteKb.locator('xpath=ancestor::tr')).toContainText('12');
  await expectNoRawKeys(page);

  // The second server is blocked: the note about the domain block shows and there is no block control.
  await items.nth(1).click();
  await expect(known.locator('.claire-detail-col h3')).toContainText('c.example');
  await expect(known.locator('.claire-detail-col .claire-note.warn').first()).toContainText('c.example');
  await expect(known.locator('[data-block-known]')).toHaveCount(0);
  await expect(field('software')).toHaveText('—');
  await expect(field('created')).toContainText('2023');
  await expect(field('last_contact')).toHaveText('—');

  // Add a server: the body has only "domain"; the text tells that the server was added, and the new server is the selected one.
  await known.locator('#admin-known-domain').fill('d.example');
  await known.locator('#admin-known-add-form button[type="submit"]').click();
  await expect(page.locator('.claire-toast').last()).toContainText('d.example 서버를 알려진 서버에 추가했습니다.');
  expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/admin/federation/known-servers').map((c) => c.body)).toEqual([{ domain: 'd.example' }]);
  await expect(known.locator('[data-pick-known]')).toHaveCount(3);
  await expect(known.locator('.claire-detail-col h3')).toContainText('d.example');
  await expect(field('source')).toHaveText('관리자가 도메인을 추가함');

  // The same domain again: the text tells that the server is known.
  await known.locator('#admin-known-domain').fill('d.example');
  await known.locator('#admin-known-add-form button[type="submit"]').click();
  await expect(page.locator('.claire-toast').last()).toContainText('d.example 서버는 이미 알려진 서버입니다.');

  // The domain of this server: the catalog text of the error, not the code.
  await known.locator('#admin-known-domain').fill('127.0.0.1');
  await known.locator('#admin-known-add-form button[type="submit"]').click();
  await expect(page.locator('.claire-toast').last()).toContainText('이 서버의 도메인입니다.');
  await expect(page.locator('.claire-toast').last()).not.toContainText('own_domain');

  // Block a server: no request goes out when the confirmation is dismissed; after the confirmation the body has the domain and a note.
  const confirms = [];
  let accept = false;
  page.on('dialog', (dialog) => {
    confirms.push(dialog.message());
    return accept ? dialog.accept() : dialog.dismiss();
  });
  await known.locator('[data-block-known="d.example"]').click();
  await expect.poll(() => confirms.length).toBe(1);
  expect(confirms[0]).toContain('d.example 서버를 차단할까요?');
  expect(calls.some((c) => c.method === 'POST' && c.path === '/api/admin/federation/domain-blocks')).toBe(false);
  accept = true;
  await known.locator('[data-block-known="d.example"]').click();
  await expect(page.locator('.claire-toast').last()).toContainText('d.example 서버를 차단했습니다.');
  expect(calls.find((c) => c.method === 'POST' && c.path === '/api/admin/federation/domain-blocks').body).toEqual({ domain: 'd.example', note: '알려진 서버 목록에서 차단' });
  await expect(known.locator('.claire-detail-col h3')).toContainText('차단됨');
  await expect(known.locator('[data-block-known]')).toHaveCount(0);
  // The domain blocks tab has the new block.
  await tab(page, 'blocks').click();
  await expect(page.locator('#admin-federation-domain-blocks-tbody [data-domain-block="d.example"]')).toBeVisible();
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('/admin/federation: add and remove a domain block; a change that needs a recent sign-in opens the step-up dialog and goes one more time', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'fedadmin2');
  const { calls, state } = await installApi(page, { reauth: true, reauthAnswers: [{ status: 403, error: 'invalid_password' }, { status: 429, error: 'too_many_attempts' }, { status: 409, error: 'password_not_set' }, { status: 500, error: 'server_fault_code' }], noBlocks: true });
  state.servers.push(knownServer('e.example'));
  await page.goto('/admin/federation', { waitUntil: 'domcontentloaded' });
  await tab(page, 'blocks').click();
  const blocks = page.locator('#admin-federation-domain-blocks');
  const rows = page.locator('#admin-federation-domain-blocks-tbody tr[data-domain-block]');
  const result = page.locator('#admin-federation-domain-blocks-result');
  await expect(rows).toHaveCount(0);
  await expect(page.locator('#admin-federation-domain-blocks-tbody')).toContainText('차단한 도메인이 없습니다');

  // Add a block: the server answers 401 "reauth_required", thus the step-up dialog opens and the result line tells why.
  await blocks.locator('input[name="domain"]').fill('e.example');
  await blocks.locator('input[name="note"]').fill('abuse');
  await blocks.locator('#admin-federation-domain-block-form button[type="submit"]').click();
  const modal = page.locator('#console-reauth-modal');
  await expect(modal).toBeVisible();
  await expect(result).not.toHaveText('');
  await expect(result).not.toContainText('reauth_required');
  const modalError = page.locator('#console-reauth-result');
  // A wrong password, then too many attempts: the dialog stays and shows the catalog text of each code, not the code.
  await page.locator('#console-reauth-password').fill('wrong-password');
  await modal.locator('button[type="submit"]').click();
  await expect(modalError).toHaveText('비밀번호가 맞지 않습니다.');
  await expect(modal).toBeVisible();
  await page.locator('#console-reauth-password').fill('wrong-password-2');
  await modal.locator('button[type="submit"]').click();
  await expect(modalError).toHaveText('시도가 너무 많습니다. 잠시 뒤에 다시 시도하세요.');
  // An account without a password, and a code that has no text: the text for the account, and the general text. No code shows.
  await modal.locator('button[type="submit"]').click();
  await expect(modalError).toHaveText('비밀번호가 아직 없습니다. 비밀번호 설정을 바로 하세요.');
  await modal.locator('button[type="submit"]').click();
  await expect(modalError).toHaveText('재인증에 실패했습니다.');
  // The correct password: the dialog closes and the same change goes one more time.
  await page.locator('#console-reauth-password').fill('e2e-password');
  await modal.locator('button[type="submit"]').click();
  await expect(modal).toBeHidden();
  await expect(rows).toHaveCount(1);
  await expect(page.locator('#admin-federation-domain-blocks-tbody [data-domain-block="e.example"]')).toContainText('abuse');
  await expect(page.locator('#admin-federation-domain-blocks-tbody [data-domain-block="e.example"]')).toContainText('2023');
  await expect(result).toContainText('e.example');
  await expect(result).toHaveAttribute('data-state', 'ok');
  const posts = calls.filter((c) => c.method === 'POST' && c.path === '/api/admin/federation/domain-blocks');
  expect(posts.map((c) => c.body)).toEqual([{ domain: 'e.example', note: 'abuse' }, { domain: 'e.example', note: 'abuse' }]);
  expect(calls.filter((c) => c.path === '/api/auth/reauth').map((c) => c.body)).toEqual([{ password: 'wrong-password' }, { password: 'wrong-password-2' }, { password: 'wrong-password-2' }, { password: 'wrong-password-2' }, { password: 'e2e-password' }]);
  await expect(blocks.locator('input[name="domain"]')).toHaveValue('');

  // The same domain again: the text tells that the block is there.
  await blocks.locator('input[name="domain"]').fill('e.example');
  await blocks.locator('#admin-federation-domain-block-form button[type="submit"]').click();
  await expect(result).toContainText('이미');
  await expect(rows).toHaveCount(1);
  // The known servers tab reads again after a block: the server e.example is blocked.
  await tab(page, 'known').click();
  await expect(page.locator('#admin-federation-known-host [data-pick-known="e.example"]')).toContainText('차단됨');
  await tab(page, 'blocks').click();

  // A domain that is not valid: the catalog text of the error.
  await blocks.locator('input[name="domain"]').fill('bad domain');
  await blocks.locator('#admin-federation-domain-block-form button[type="submit"]').click();
  await expect(result).toHaveAttribute('data-state', 'error');
  await expect(result).not.toContainText('invalid_domain');
  await expect(result).not.toHaveText('');

  // Remove a block: the confirmation names the domain; DELETE has the domain in its body.
  const confirms = [];
  page.on('dialog', (dialog) => {
    confirms.push(dialog.message());
    return dialog.accept();
  });
  await page.locator('[data-domain-block-remove="e.example"]').click();
  await expect(rows).toHaveCount(0);
  await expect(page.locator('#admin-federation-domain-blocks-tbody [data-domain-block-empty]')).toBeVisible();
  expect(confirms[0]).toContain('e.example');
  expect(calls.find((c) => c.method === 'DELETE').body).toEqual({ domain: 'e.example' });
  expect(calls.find((c) => c.method === 'DELETE').path).toBe('/api/admin/federation/domain-blocks');
  await expect(result).toContainText('e.example');
  // The known servers tab reads again: the server e.example is active again.
  await tab(page, 'known').click();
  await expect(page.locator('#admin-federation-known-host [data-pick-known="e.example"]')).toContainText('활성');
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('/admin/federation: the journal filter sends each state, replay shows only for a replayable row, and "more" sends the cursor', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'fedadmin3');
  const { calls } = await installApi(page);
  await page.goto('/admin/federation', { waitUntil: 'domcontentloaded' });
  await tab(page, 'journal').click();
  const journal = page.locator('#admin-federation-journal-host');
  const rows = journal.locator('tbody tr');
  await expect(rows).toHaveCount(6);
  await expect(journal.locator('thead th')).toHaveText(['액티비티', '액터', '상태', '시도 횟수', '수신 시각', '']);
  // The first request has no filter and no cursor.
  expect(calls.filter((c) => c.path.startsWith('/api/admin/federation/journal'))[0].path).toBe('/api/admin/federation/journal');

  // One row: the activity with its kind, the actor, the state as text with the catalog text of the error code, the detail, the attempts, and the time in the format of the locale.
  await expect(rows.nth(0)).toContainText('Offer');
  await expect(rows.nth(0)).toContainText('offer_contribution');
  await expect(rows.nth(0)).toContainText('https://b.example/ap/actors/7');
  await expect(rows.nth(0).locator('.claire-tag')).toHaveText('실패');
  await expect(rows.nth(0).locator('[data-journal-code="handler_error"]')).toHaveText('처리 중에 오류가 났습니다. 이 서버는 변경을 취소했습니다.');
  await expect(rows.nth(0)).toContainText('KeyError: document');
  await expect(rows.nth(0)).toContainText('2026');
  await expect(rows.nth(0)).not.toContainText('T10:00:00Z');
  await expect(rows.nth(1).locator('.claire-tag')).toHaveText('처리됨');
  await expect(rows.nth(2).locator('.claire-tag')).toHaveText('수신됨');
  await expect(rows.nth(3).locator('.claire-tag')).toHaveText('거부됨');
  // The refusal codes of the handlers and of the controls show as catalog text: a blocked domain at a replay, a revision before the minimum time, a follow over the limit of followers.
  await expect(rows.nth(3).locator('[data-journal-code="blocked_domain"]')).toHaveText('관리자가 액터의 도메인을 차단했습니다.');
  await expect(rows.nth(4).locator('.claire-tag')).toHaveText('거부됨');
  await expect(rows.nth(4).locator('[data-journal-code="revision_too_soon"]')).toHaveText('사본이 최소 간격보다 짧은 시간 안에 개정을 받았습니다 (remote_revision_min_seconds).');
  await expect(rows.nth(5).locator('[data-journal-code="follower_limit"]')).toHaveText('지식 베이스의 팔로워 수가 한도에 이르렀습니다 (kb_followers_limit).');
  // No row shows a code as its text; the code is in the attribute "title".
  for (const el of await journal.locator('[data-journal-code]').all()) {
    const code = await el.getAttribute('data-journal-code');
    await expect(el).toHaveAttribute('title', code);
    await expect(journal.locator('tbody')).not.toContainText(code);
  }
  // Replay: only for the rows with "replayable" (failed, received).
  await expect(journal.locator('[data-replay-j]')).toHaveCount(2);
  await expect(rows.nth(0).locator('[data-replay-j="40"]')).toBeVisible();
  await expect(rows.nth(1).locator('[data-replay-j]')).toHaveCount(0);
  await expect(rows.nth(2).locator('[data-replay-j="38"]')).toBeVisible();
  await expect(rows.nth(3).locator('[data-replay-j]')).toHaveCount(0);
  await expect(rows.nth(4).locator('[data-replay-j]')).toHaveCount(0);

  // More: the request has the cursor "next_max_id"; the rows of the next page go below, and the control goes away when there is no next page.
  await journal.locator('[data-journal-more]').click();
  await expect(rows).toHaveCount(7);
  expect(calls.some((c) => c.path === '/api/admin/federation/journal?max_id=35')).toBe(true);
  await expect(rows.nth(6)).toContainText('Update');
  await expect(journal.locator('[data-journal-more]')).toHaveCount(0);

  // Replay of a failed row: POST with an empty object; the row gets the new state and loses the control.
  await rows.nth(0).locator('[data-replay-j="40"]').click();
  await expect(rows.nth(0).locator('.claire-tag')).toHaveText('처리됨');
  await expect(rows.nth(0).locator('[data-replay-j]')).toHaveCount(0);
  const replayCall = calls.find((c) => c.method === 'POST' && c.path === '/api/admin/federation/journal/40/replay');
  expect(replayCall.body).toEqual({});
  await expect(page.locator('.claire-toast').last()).toContainText('새 상태: 처리됨');
  // The rows of the second page stay after the replay.
  await expect(rows).toHaveCount(7);
  // A replay that the server refuses: the catalog text of the code.
  await rows.nth(2).locator('[data-replay-j="38"]').click();
  await expect(page.locator('.claire-toast').last()).not.toContainText('federation_off');
  await expect(page.locator('.claire-toast').last()).toContainText('연합');
  await expect(rows.nth(2).locator('[data-replay-j="38"]')).toBeEnabled();

  // The state filter: each state sends "status"; "all" sends no parameter.
  const chip = (value) => journal.locator('input[name="fed-journal-status"][value="' + value + '"]').locator('..');
  await expect(journal.locator('.claire-chip')).toHaveText(['전체', '수신됨', '처리됨', '실패', '거부됨']);
  for (const [state, count] of [['received', 1], ['processed', 1], ['failed', 1], ['refused', 3]]) {
    await chip(state).click();
    await expect(rows).toHaveCount(count);
    await expect(journal.locator('input[name="fed-journal-status"][value="' + state + '"]')).toBeChecked();
    expect(calls[calls.length - 1].path).toBe('/api/admin/federation/journal?status=' + state);
    await expect(journal.locator('[data-journal-more]')).toHaveCount(0);
  }
  await chip('all').click();
  await expect(rows).toHaveCount(6);
  expect(calls[calls.length - 1].path).toBe('/api/admin/federation/journal');
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('/admin/federation: the switch of the automatic contribution reads its state and sends the new state', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'fedadmin4');
  const { calls } = await installApi(page);
  await page.goto('/admin/federation', { waitUntil: 'domcontentloaded' });
  const toggle = page.locator('#admin-fed-auto-contrib-toggle');
  await expect(toggle).toBeVisible();
  await expect(toggle).toBeChecked();
  // The switch has a visible label.
  await expect(page.locator('label[for="admin-fed-auto-contrib-toggle"]')).toBeVisible();
  await toggle.click();
  await expect.poll(() => calls.find((c) => c.method === 'PUT' && c.path === '/api/admin/auto-contribution')?.body).toEqual({ state: 'off' });
  await expect(toggle).not.toBeChecked();
  await expect(page.locator('.claire-toast').last()).toBeVisible();
  await toggle.click();
  await expect.poll(() => calls.filter((c) => c.method === 'PUT').map((c) => c.body)).toEqual([{ state: 'off' }, { state: 'on' }]);
  await expect(toggle).toBeChecked();
  await expectNoRawKeys(page);

  // Narrow screen: each tab can be reached and the controls of a panel are inside the screen.
  await page.setViewportSize({ width: 390, height: 800 });
  for (const id of ['known', 'blocks', 'journal']) {
    await tab(page, id).scrollIntoViewIfNeeded();
    await tab(page, id).click();
    await expect(panel(page, id)).toBeVisible();
  }
  const replayBox = await page.locator('#admin-federation-journal-host [data-replay-j]').first().boundingBox();
  expect(replayBox).not.toBeNull();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await tab(page, 'known').click();
  const addBox = await page.locator('#admin-known-add-form button[type="submit"]').boundingBox();
  expect(addBox.x + addBox.width).toBeLessThanOrEqual(391);
  expect(errors).toEqual([]);
});

test('the step-up dialog of the shared request function: the catalog text for each error code, and the request goes one more time after the sign-in', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'fedadmin5');
  const { calls } = await installApi(page, { reauth: true, reauthAnswers: [{ status: 403, error: 'invalid_password' }, { status: 409, error: 'password_not_set' }, { status: 429, error: 'too_many_attempts' }] });
  await page.goto('/admin/federation', { waitUntil: 'domcontentloaded' });
  await tab(page, 'known').click();
  const known = page.locator('#admin-federation-known-host');
  // Add a server: POST gives 401 "reauth_required", thus the dialog of shared_ui.js opens.
  await known.locator('#admin-known-domain').fill('f.example');
  await known.locator('#admin-known-add-form button[type="submit"]').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg).toBeVisible();
  await expect(dlg.locator('h2')).toContainText('최근 로그인이 필요합니다');
  await expect(dlg.locator('label[for="claire-stepup-pw"]')).toHaveText('계정 비밀번호');
  const pw = dlg.locator('#claire-stepup-pw');
  const err = dlg.locator('#claire-stepup-err');
  const submit = dlg.locator('.claire-dlg-foot .claire-btn.pri');
  await expect(err).toBeHidden();

  // 403 "invalid_password": the text of the catalog, and the field is marked as not valid.
  await pw.fill('wrong-password');
  await submit.click();
  await expect(err).toHaveText('비밀번호가 맞지 않습니다.');
  await expect(pw).toHaveAttribute('aria-invalid', 'true');
  await expect(dlg).toBeVisible();
  await expect(submit).toBeEnabled();
  // 409 "password_not_set". The Enter key in the field sends the form also after a failure.
  await pw.fill('wrong-password-2');
  await pw.press('Enter');
  await expect(err).toContainText('계정에 비밀번호가 없어');
  await expect(err).not.toContainText('password_not_set');
  // 429 "too_many_attempts": a text, not the code.
  await pw.press('Enter');
  await expect(err).toHaveText('시도가 너무 많습니다. 잠시 뒤에 다시 시도하세요.');
  await expectNoRawKeys(page);
  // The correct password: the dialog closes, and POST goes one more time with the same body.
  await pw.fill('e2e-password');
  await pw.press('Enter');
  await expect(dlg).toHaveCount(0);
  await expect(page.locator('.claire-toast').last()).toContainText('f.example 서버를 알려진 서버에 추가했습니다.');
  expect(calls.filter((c) => c.method === 'POST' && c.path === '/api/admin/federation/known-servers').map((c) => c.body)).toEqual([{ domain: 'f.example' }, { domain: 'f.example' }]);
  expect(calls.filter((c) => c.path === '/api/auth/reauth').map((c) => c.body)).toEqual([{ password: 'wrong-password' }, { password: 'wrong-password-2' }, { password: 'wrong-password-2' }, { password: 'e2e-password' }]);
  await expect(known.locator('[data-pick-known="f.example"]')).toBeVisible();
  expect(errors).toEqual([]);
});

test('the step-up dialog of the shared request function: a cancel sends no second request and shows the text of the cancel', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  await authenticateOwner(page, 'fedadmin6');
  const { calls } = await installApi(page, { reauth: true });
  await page.goto('/admin/federation', { waitUntil: 'domcontentloaded' });
  await tab(page, 'journal').click();
  const journal = page.locator('#admin-federation-journal-host');
  await journal.locator('[data-replay-j="40"]').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg).toBeVisible();
  await dlg.locator('.claire-dlg-foot .claire-btn.sec').click();
  await expect(dlg).toHaveCount(0);
  await expect(page.locator('.claire-toast').last()).toContainText('다시 로그인하지 않았습니다');
  expect(calls.filter((c) => c.method === 'POST' && c.path.endsWith('/replay'))).toHaveLength(1);
  expect(calls.some((c) => c.path === '/api/auth/reauth')).toBe(false);
  // The control is ready for a new attempt.
  await expect(journal.locator('[data-replay-j="40"]')).toBeEnabled();
});

test('/admin/federation: remove a known server with the confirmation of what is deleted and what stays, the step-up path, the counts, and 404', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'fedadmin7');
  // The first change gets 401 "reauth_required". The domain c.example was removed by another request: DELETE gives 404 "known_server_not_found".
  const { calls } = await installApi(page, { reauth: true, goneDomains: ['c.example'] });
  await page.goto('/admin/federation', { waitUntil: 'domcontentloaded' });
  await tab(page, 'known').click();
  const known = page.locator('#admin-federation-known-host');
  await expect(known.locator('[data-pick-known]')).toHaveCount(2);
  const removeBtn = known.locator('[data-remove-known="b.example"]');
  await expect(removeBtn).toBeVisible();
  const deletes = () => calls.filter((c) => c.method === 'DELETE' && c.path === '/api/admin/federation/known-servers');

  // The confirmation names the server, and lists what is deleted and what stays. A cancel sends no request.
  await removeBtn.click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg).toBeVisible();
  await expect(dlg.locator('h2')).toContainText('b.example');
  await expect(dlg.locator('[data-remove-deleted] li')).toHaveText([
    '알려진 서버 항목.',
    '그 서버의 공유 지식 베이스 기록과 그 테마 소속.',
    '그 지식 베이스들의 문서 목록과 읽기 상태.',
    '이 서버의 공유 지식 베이스를 팔로우하는 그 서버의 서버 액터.',
    '그 서버로 전달을 기다리는 액티비티.',
    '그 서버에서 온 처리됨·거부됨 저널 행의 액티비티 본문.'
  ]);
  await expect(dlg.locator('[data-remove-stays] li')).toHaveCount(4);
  await expect(dlg.locator('[data-remove-stays] li').nth(0)).toContainText('사본');
  await expect(dlg.locator('[data-remove-stays] li').nth(1)).toContainText('팔로우');
  await expect(dlg.locator('[data-remove-stays] li').nth(2)).toContainText('실패한 행');
  await expect(dlg.locator('[data-remove-stays] li').nth(3)).toContainText('도메인 차단');
  await expect(dlg).toContainText('다시 알려진 서버가 됩니다');
  await expectNoRawKeys(page);
  await dlg.locator('.claire-dlg-foot .claire-btn.sec').click();
  await expect(dlg).toHaveCount(0);
  expect(deletes()).toHaveLength(0);

  // The confirmation: DELETE gets 401 "reauth_required", the step-up dialog opens, and after the sign-in the same DELETE goes one more time.
  await removeBtn.click();
  await expect(dlg.locator('.claire-dlg-foot .claire-btn.danger')).toHaveText('서버 제거');
  await dlg.locator('.claire-dlg-foot .claire-btn.danger').click();
  await expect(dlg.locator('h2')).toContainText('최근 로그인이 필요합니다');
  await dlg.locator('#claire-stepup-pw').fill('e2e-password');
  await dlg.locator('#claire-stepup-pw').press('Enter');
  await expect(dlg).toHaveCount(0);
  await expect(page.locator('.claire-toast').last()).toContainText('b.example 서버를 제거했습니다. 삭제: 지식 베이스 기록 1개, 문서 목록 항목 12개, 팔로워 2개, 전달 대기 액티비티 3개, 저널 본문 4개.');
  expect(deletes().map((c) => c.body)).toEqual([{ domain: 'b.example' }, { domain: 'b.example' }]);
  expect(calls.filter((c) => c.path === '/api/auth/reauth').map((c) => c.body)).toEqual([{ password: 'e2e-password' }]);
  // The list reads again: the server is gone, and the next server is the selected one.
  await expect(known.locator('[data-pick-known]')).toHaveCount(1);
  await expect(known.locator('[data-pick-known="b.example"]')).toHaveCount(0);
  await expect(known.locator('.claire-detail-col h3')).toContainText('c.example');

  // A blocked server can be removed too. 404 "known_server_not_found": the catalog text, not the code, and the list reads again.
  await known.locator('[data-remove-known="c.example"]').click();
  await dlg.locator('.claire-dlg-foot .claire-btn.danger').click();
  await expect(page.locator('.claire-toast').last()).toContainText('이 도메인은 알려진 서버 목록에 없습니다.');
  await expect(page.locator('.claire-toast').last()).not.toContainText('known_server_not_found');
  expect(deletes().map((c) => c.body)).toEqual([{ domain: 'b.example' }, { domain: 'b.example' }, { domain: 'c.example' }]);
  await expect(known.locator('[data-pick-known]')).toHaveCount(0);
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('/admin/federation: readiness checks GF1-GF20 in three groups with levels, not-applicable rows, warning items, the party of a failed answer, and the sidecar state', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'fedadmin8');
  await installApi(page);
  const nowSec = Math.floor(Date.now() / 1000);
  await page.route((u) => u.pathname === '/api/admin/federation', (route) => {
    const items = [
      { id: 'GF1', name: 'Server mode allows federation', group: 'account', level: 'required', applies: true, passed: true, code: 'mode_network', detail: 'The server mode is network.', values: {} },
      { id: 'GF2', name: 'Sidecar heartbeat and contract version', group: 'account', level: 'required', applies: true, passed: true, code: 'heartbeat_ok', detail: 'Heartbeat ok.', values: { expected: '1' } },
      { id: 'GF3', name: 'Service key file', group: 'account', level: 'required', applies: true, passed: true, code: 'service_key_ok', detail: 'Service key ok.', values: {} },
      { id: 'GF4', name: 'Master key', group: 'account', level: 'required', applies: true, passed: true, code: 'master_key_ok', detail: 'Master key ok.', values: {} },
      { id: 'GF5', name: 'Canonical origin uses HTTPS in production', group: 'account', level: 'required', applies: true, passed: true, code: 'https_ok', detail: 'HTTPS ok.', values: {} },
      { id: 'GF6', name: 'Sidecar public self-check', group: 'account', level: 'required', applies: true, passed: true, code: 'webfinger_self_check_ok', detail: 'WebFinger ok.', values: {} },
      { id: 'GF7', name: 'OAuth metadata through the public origin', group: 'account', level: 'required', applies: true, passed: true, code: 'oauth_metadata_ok', detail: 'Metadata ok.', values: {} },
      // A failed item has the code of the party that answered (FEDERATION_GO_LIVE_DESIGN.md Section 5.3) and the values of its text.
      { id: 'GF8', name: 'Knowledge federation paths reach this sidecar', group: 'knowledge', level: 'required', applies: true, passed: false, code: 'intermediary', detail: 'An intermediary answered.', values: { path: '/ap/kb', party: 'intermediary', source: 'sidecar', status: 403, server: 'waf', paths: '/ap/kb, /ap/themes' } },
      { id: 'GF9', name: 'Signed request through the public origin', group: 'knowledge', level: 'required', applies: true, passed: true, code: 'signed_ok', detail: 'Signed ok.', values: {} },
      { id: 'GF10', name: 'Inbox body of the largest size through the public origin', group: 'knowledge', level: 'required', applies: true, passed: true, code: 'inbox_body_ok', detail: 'Inbox body ok.', values: { bytes: 1040384 } },
      { id: 'GF11', name: 'Knowledge federation loops of the sidecar make progress', group: 'knowledge', level: 'required', applies: true, passed: true, code: 'loops_ok', detail: 'Loops ok.', values: {} },
      { id: 'GF12', name: 'Actor keys can be decrypted', group: 'knowledge', level: 'required', applies: true, passed: true, code: 'actor_keys_empty', detail: 'No actor key yet.', values: {} },
      { id: 'GF13', name: 'Settings of the knowledge federation and the public name', group: 'knowledge', level: 'required', applies: true, passed: true, code: 'settings_ok', detail: 'Settings ok.', values: {} },
      { id: 'GF14', name: 'First-contact documents through the public origin', group: 'knowledge', level: 'required', applies: false, passed: true, code: 'federation_off', detail: 'Federation is off.', values: {} },
      { id: 'GF15', name: 'Knowledge base actor document through the public origin', group: 'knowledge', level: 'required', applies: false, passed: true, code: 'federation_off', detail: 'Federation is off.', values: {} },
      { id: 'GF16', name: 'Collections refuse an unsigned read', group: 'knowledge', level: 'required', applies: false, passed: true, code: 'federation_off', detail: 'Federation is off.', values: {} },
      { id: 'GF17', name: 'JSON-LD context and vocabulary page through the public origin', group: 'knowledge', level: 'warning', applies: true, passed: false, code: 'ns_context_failed', detail: 'Missing CORS header.', values: { problem: 'missing header Access-Control-Allow-Origin: *' } },
      { id: 'GF18', name: 'The checks take the path of other servers', group: 'operation', level: 'warning', applies: true, passed: false, code: 'public_host_private', detail: 'Private address.', values: { host: 'claire.example.com', address: '192.168.10.5', source: 'sidecar' } },
      { id: 'GF19', name: 'Other servers reach this server', group: 'operation', level: 'warning', applies: false, passed: true, code: 'federation_off', detail: 'Federation is off.', values: {} },
      { id: 'GF20', name: 'Deliveries to other servers succeed', group: 'operation', level: 'warning', applies: false, passed: true, code: 'federation_off', detail: 'Federation is off.', values: {} },
    ];
    return json(route, {
      federation: 'off',
      mode: 'network',
      readiness: { passed: false, items },
      heartbeat: {
        received_at: nowSec - 3,
        contract_version: '1.13',
        sidecar_version: '0.1.0',
        queue_depth: 0,
        marker: '5b1f6f0e-3c56-4d0a-9d55-0c1a2b3c4d5e',
        public_host: { host: 'claire.example.com', addresses: [{ address: '192.168.10.5', class: 'private' }], error: null },
        self_check: { ok: true, checked_at: nowSec - 3, answer: null },
        knowledge_self_check: { checked_at: nowSec - 3, failed_paths: ['/ap/kb', '/ap/themes'], answers: [], signed: { draft_cavage: { result: 'ok', answer: null }, rfc9421: { result: 'ok', answer: null } } },
        traffic: { window_seconds: 86400, inbound_verified: { count: 4, hosts: 2, last_at: nowSec - 60 }, inbound_rejected: { count: 1, last_at: nowSec - 90 }, deliveries: { succeeded: 3, failed: { dns: 0, tls: 0, timeout: 2, http_4xx: 0, http_5xx: 0, other: 0 }, last_success_at: nowSec - 30 } },
        loops: {
          knowledge_reads: { running: false, progress_at: nowSec - 12, error: null, error_at: null },
          knowledge_exchange: { running: false, progress_at: nowSec - 40, error: 'ECONNREFUSED', error_at: nowSec - 20 },
        },
      },
    });
  });

  await page.goto('/admin/federation', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#admin-federation-readiness-result')).toContainText('통과하지 못한 필수 준비 검사가 있습니다.');
  await expect(page.locator('[data-readiness-group="account"]')).toContainText('계정 연합 (GF1–GF7)');
  await expect(page.locator('[data-readiness-group-summary="account"]')).toContainText('필수 7/7 통과');
  await expect(page.locator('[data-readiness-group="knowledge"]')).toContainText('공유 지식 연합 (GF8–GF17)');
  await expect(page.locator('[data-readiness-group-summary="knowledge"]')).toContainText('필수 5/6 통과');
  await expect(page.locator('[data-readiness-group-summary="knowledge"]')).toContainText('1개 실패');
  await expect(page.locator('[data-readiness-group-summary="knowledge"]')).toContainText('경고 1개');
  await expect(page.locator('[data-readiness-group-summary="knowledge"]')).toContainText('3개 해당 없음');

  const gf14 = page.locator('tr[data-readiness-item="GF14"]');
  await expect(gf14).toHaveAttribute('data-readiness-applies', '0');
  await expect(gf14).toContainText('해당 없음');

  const gf17 = page.locator('tr[data-readiness-item="GF17"]');
  await expect(gf17).toHaveAttribute('data-readiness-level', 'warning');
  await expect(gf17).toHaveAttribute('data-readiness-passed', '0');
  await expect(gf17).toContainText('경고');
  await expect(gf17).toContainText('실패');

  // The failed item tells which party answered, what to do, and all failed paths.
  const gf8 = page.locator('tr[data-readiness-item="GF8"]');
  await expect(gf8).toHaveAttribute('data-readiness-passed', '0');
  await expect(gf8).toContainText('중간 장비가 /ap/kb에 HTTP 403로 응답했습니다 (Server: waf).');
  await expect(gf8).toContainText('WAF라면 로그에서 규칙 ID를 찾으세요.');
  await expect(gf8).toContainText('실패한 경로: /ap/kb, /ap/themes.');

  // The third group has the path and the traffic checks. They are warnings.
  await expect(page.locator('[data-readiness-group="operation"]')).toContainText('경로와 트래픽 (GF18–GF20)');
  await expect(page.locator('[data-readiness-group-summary="operation"]')).toContainText('경고 1개');
  await expect(page.locator('[data-readiness-group-summary="operation"]')).toContainText('2개 해당 없음');
  const gf18 = page.locator('tr[data-readiness-item="GF18"]');
  await expect(gf18).toHaveAttribute('data-readiness-level', 'warning');
  await expect(gf18).toContainText('공개 호스트 claire.example.com가 sidecar에서 사설 주소 192.168.10.5로 해석됩니다.');
  await expect(gf18).toContainText('ops/federation_outside_probe.sh');

  // The sidecar card shows the path record and the traffic counters of the heartbeat.
  await expect(page.locator('#admin-federation-heartbeat [data-sidecar-row="public_host"]')).toContainText('192.168.10.5 (private)');
  await expect(page.locator('#admin-federation-heartbeat [data-sidecar-row="traffic_inbound"]')).toContainText('서버 2곳에서 4건 검증 · 검증 실패 1건');
  await expect(page.locator('#admin-federation-heartbeat [data-sidecar-row="traffic_deliveries"]')).toContainText('성공 3건 · 실패한 시도 2건');

  await expect(page.locator('#admin-federation-heartbeat [data-sidecar-loop="knowledge_reads"]')).toContainText('없음');
  await expect(page.locator('#admin-federation-heartbeat [data-sidecar-loop="knowledge_exchange"]')).toContainText('ECONNREFUSED');
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});
