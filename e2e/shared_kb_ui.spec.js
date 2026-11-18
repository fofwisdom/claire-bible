const { test, expect } = require('@playwright/test');

test.use({ serviceWorkers: 'block' });

// The pages of the shared knowledge bases against answers in the shapes of docs/implementation/API_HTTP_CONTRACT.md (units CT-1 to CT-13). The answers are fixed data: the E2E database has no shared knowledge base. A page navigation always goes to the server; only the JSON requests of the page scripts get the fixed data.

async function authenticateOwner(page, n) {
  await page.goto('/?t=e2e-sharedkb' + n + '-token-0123456789abc', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
}

function json(route, body, status) {
  return route.fulfill({ status: status || 200, contentType: 'application/json', body: JSON.stringify(body) });
}

async function installApi(page) {
  const calls = [];
  const shared = [
    { number: '101', label: 'Modern History', description: 'History of the 19th century', knowledge_type: 'real', join_policy: 'approval', state: 'active', seed_entities: ['Railway'], is_personal: false, is_public: true, role: 'moderator', membership_state: 'active', url: 'http://127.0.0.1:8766/kb/101/', stats: { documents: 30, entities: 120, relations: 80 }, active_moderators: 1, reembed_required: false, embed_provider: 'gemini', embed_model: 'gemini-embedding-001' },
    { number: '102', label: 'Fantasy Lore', description: '', knowledge_type: 'fictional', join_policy: 'open', state: 'active', seed_entities: [], is_personal: false, is_public: true, role: null, membership_state: 'pending', url: 'http://127.0.0.1:8766/kb/102/', stats: { documents: 4, entities: 9, relations: 3 }, active_moderators: 0, reembed_required: false, embed_provider: '', embed_model: '' }
  ];
  const personal = { number: '10', label: 'Personal Research', is_personal: true, is_public: false, knowledge_type: null, stats: { documents: 12, entities: 44, relations: 30 } };
  const themes = [
    { number: '201', url: 'http://127.0.0.1:8766/themes/201', label: 'Railway · Steam', description: 'Railway, Steam, Bridge', knowledge_type: 'real', state: 'active', knowledge_bases: 1 },
    { number: '202', url: 'http://127.0.0.1:8766/themes/202', label: 'Dragons · Elves', description: 'Dragons, Elves', knowledge_type: 'fictional', state: 'active', knowledge_bases: 1 }
  ];
  let rule = null;

  await page.route((u) => ['/kb', '/themes', '/submissions', '/documents'].includes(u.pathname) || /^\/kb\/\d+\//.test(u.pathname) || u.pathname.startsWith('/api/auto-') || ['/api/reputation', '/api/contributions', '/api/mutual-collaborations', '/api/contribution-blocks', '/api/admin/themes', '/api/admin/reputations', '/api/admin/auto-contribution', '/api/kb/similar'].includes(u.pathname), async (route) => {
    const req = route.request();
    if (req.resourceType() === 'document') return route.fallback();
    // The workspace reads its own document list from the server; only the console pages get the fixed list.
    if (new URL(req.url()).pathname === '/documents' && !new URL(page.url()).pathname.startsWith('/settings')) return route.fallback();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    calls.push({ method, path: path + url.search, body: req.postData() ? JSON.parse(req.postData()) : null });
    if (path === '/kb' && method === 'GET') {
      const joined = url.searchParams.get('joined') === 'true';
      const type = url.searchParams.get('type');
      const items = shared.filter((k) => (!joined || k.role) && (!type || k.knowledge_type === type));
      return json(route, { type: 'OrderedCollection', totalItems: items.length, knowledge_bases: items, personal_kb: personal, multi_theme: true });
    }
    // GET /api/kb/similar: the shared knowledge bases of this server and of other servers near the subject (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 4). The fixed answer has the ones of the type and one of the server b.example.
    if (path === '/api/kb/similar') {
      const type = url.searchParams.get('type');
      const matched = !!url.searchParams.get('label') || url.searchParams.getAll('seed').length > 0;
      const items = shared.filter((k) => k.knowledge_type === type).map((k) => ({ remote: false, server: null, number: k.number, label: k.label, description: k.description, knowledge_type: k.knowledge_type, url: k.url, score: matched ? 0.5 : null }));
      items.push({ remote: true, server: 'b.example', number: null, label: 'Dragon Archive', description: '', knowledge_type: type, url: 'https://b.example/kb/900/', score: matched ? 0.4 : null });
      return json(route, { matched, knowledge_bases: items });
    }
    if (path === '/kb' && method === 'POST') return json(route, { ok: true, knowledge_base: { number: '103', label: 'New' } }, 201);
    if (/^\/kb\/\d+\/$/.test(path) && method === 'DELETE') {
      if (url.searchParams.get('apply') === 'true') return json(route, { ok: true, applied: true });
      return json(route, { ok: true, applied: false, would_delete: { documents: 30, entities: 120, relations: 80, members: 5, contributions: 31 } });
    }
    // GET /kb/{number}/ with "Accept: application/json": the item of one knowledge base.
    if (/^\/kb\/\d+\/$/.test(path) && method === 'GET') {
      const kb = shared.find((k) => path === '/kb/' + k.number + '/');
      return kb ? json(route, { knowledge_base: kb }) : json(route, { error: 'kb_not_found' }, 404);
    }
    if (path === '/kb/101/moderation-log') return json(route, { log: [{ number: '2', action: 'contribution_reject', actor: { account: '5550001', display_name: 'Bob' }, target: '9004', reason: 'off_subject', created_at: 1700000100 }, { number: '1', action: 'settings', actor: null, target: 'label', reason: '', created_at: 1700000000 }] });
    // GET /api/contributions: the contributions with the credit of the requester; "?origin=auto" gives the automatic ones.
    // An automatic contribution to a shared knowledge base of another server (KNOWLEDGE_FEDERATION_DESIGN.md Section 12.3) has "remote_target"; this one got a Reject because that server accepts no automatic contributions now.
    const remoteAuto = { knowledge_base: null, label: 'Railway Archive', number: null, document: 'doc-3', title: 'Steam Engine Notes', origin: 'auto', state: 'rejected', approved: false, reason: 'automatic_not_accepted', contributed_at: 1700000200, remote_target: { remote_id: 5, server: 'b.example', url: 'https://b.example/kb/901/', actor: 'https://b.example/ap/kb/901', result: null, with_grant: false, delivery: 'delivered', decided_at: 1700000300 } };
    if (path === '/api/contributions') return json(route, { contributions: url.searchParams.get('origin') === 'auto' ? [remoteAuto] : [{ knowledge_base: '101', label: 'Modern History', number: '9003', document: 'copy-3', title: 'Canal Survey 1840', origin: 'direct', state: 'visible', approved: false, reason: '', contributed_at: 1700000000 }, { knowledge_base: '101', label: 'Modern History', number: '9005', document: 'copy-5', title: 'Bridge Ledger', origin: 'bulk', state: 'visible', approved: true, reason: '', contributed_at: 1690000000 }] });
    if (/\/contributions$/.test(path)) return json(route, { contributions: url.searchParams.get('state') === 'failed' && path.startsWith('/kb/101/') ? [{ number: '9001', document: 'doc-f', origin: 'direct', state: 'failed', reason: 'embedding_model_unavailable', contributor: null }] : [] });
    if (path === '/themes') return json(route, { type: 'OrderedCollection', totalItems: themes.length, themes });
    if (path === '/submissions') return json(route, { submissions: [{ number: '7001', created_at: 1, items: [{ document: 'doc-1', title: 'Railway Timetable 1851', theme: '201', theme_label: 'Railway · Steam', routed_by: 'server', score: 0.71, targets: [{ knowledge_base: '101', label: 'Modern History', state: 'pending', contribution: '9002', approved: false }] }] }] });
    if (path === '/documents') return json(route, { documents: [{ id: 'doc-1', title: 'Railway Timetable 1851' }, { id: 'doc-2', title: 'Space Opera Draft' }] });
    if (path === '/api/auto-rule' && method === 'GET') return json(route, { server_switch: true, rule });
    if (path === '/api/auto-rule' && method === 'PUT') {
      const body = JSON.parse(req.postData());
      rule = { state: body.state, memberships: body.memberships, mutual: body.mutual, include_uploads: body.include_uploads, themes: themes.filter((t) => body.themes.includes(t.number)).map((t) => ({ number: t.number, label: t.label })), activated_at: 1 };
      return json(route, { ok: true, server_switch: true, rule, contributions: 1 });
    }
    // The dry run: a target of this server, a target of another server, and a target of a server that accepts no automatic contributions now ("closed").
    if (path === '/api/auto-rule/dry-run') return json(route, { server_switch: true, remote_state: 'on', targets: [
      { knowledge_base: '101', label: 'Modern History', remote: false, mode: 'approved', documents: [{ document: 'doc-1', title: 'Railway Timetable 1851', score: 0.71 }] },
      { knowledge_base: null, label: 'Railway Archive', remote: true, remote_id: 5, server: 'b.example', url: 'https://b.example/kb/901/', actor: 'https://b.example/ap/kb/901', mode: 'pending', documents: [{ document: 'doc-4', title: 'Steam Locomotive Log', score: 0.66 }] },
      { knowledge_base: null, label: 'Canal Archive', remote: true, remote_id: 6, server: 'c.example', url: 'https://c.example/kb/902/', actor: 'https://c.example/ap/kb/902', mode: 'closed', documents: [] }
    ] });
    if (path === '/api/contribution-blocks') return json(route, { documents: [] });
    if (path === '/api/reputation') return json(route, { level: 'limited', fiction_share: 0.25, fiction_like_documents: 1, visible_documents: 4, approval_rate: null, decisions: 0, limits: { pending: 5, auto_documents: 0 }, documents: [{ document: 'doc-2', title: 'Space Opera Draft', theme: '202', theme_label: 'Dragons · Elves' }] });
    if (path === '/api/mutual-collaborations') return json(route, { mutual_collaborations: [{ account: '5550001', display_name: 'Bob', state: 'pending', requested_by_me: false, created_at: 1, decided_at: null }] });
    if (path === '/api/admin/themes') return json(route, {
      settings: { theme_match_threshold: 0.25, document_match_threshold: 0.5, seed_match_threshold: 0.5, submission_max_documents: 20 },
      last_run_at: 1700000000,
      themes: themes.concat([{ number: '203', label: 'Canals', knowledge_type: 'real', state: 'active' }]).map((t) => ({ ...t, label_locked: false, knowledge_bases: t.number === '201' ? [{ number: '101', label: 'Modern History', is_personal: false, knowledge_type: 'real', remote: false, server: null, pinned: false, excluded: false, source: 'computed', score: 0.6 }, { number: null, label: 'Railway Archive', is_personal: false, knowledge_type: 'real', url: 'https://b.example/kb/901/', remote: true, server: 'b.example', remote_id: 5, pinned: false, excluded: false, source: 'computed', score: 0.4 }] : [] })),
      decision_agreement: [{ knowledge_base: '101', label: 'Modern History', decisions: 12, agreement: 0.75 }]
    });
    if (path === '/api/admin/reputations') return json(route, { settings: { reputation_window_days: 180 }, accounts: [{ account: '5550001', display_name: 'Bob', level: 'good', fiction_share: 0, fiction_like_documents: 0, visible_documents: 3, approval_rate: null, decisions: 0 }] });
    if (path === '/api/admin/auto-contribution') return json(route, { state: 'on', settings: { auto_match_threshold: 0.5, auto_run_documents: 50 } });
    return json(route, { ok: true });
  });
  return calls;
}

test('ingest dialog: no focus input, no knowledge base selector, the standing fiction message, and the dialog to make a shared knowledge base', async ({ page }) => {
  await authenticateOwner(page, 1);
  const calls = await installApi(page);

  await page.locator('#addbtn').click();
  await page.locator('#pop-ingest-menu [data-ingest-cmd="ingest"]').click();
  const ingestModal = page.locator('#modal-ingest');
  await expect(ingestModal).toBeVisible();
  // Design Section 8.7: the form shows the standing message each time, with the link to the fictional themes.
  await expect(ingestModal.locator('#ingest-fiction-notice')).toBeVisible();
  await expect(ingestModal.locator('#ingest-fiction-notice a[href="/themes?type=fictional"]')).toBeVisible();
  // Design Section 13.7 and Section 5.2 rule 3: no focus input and no knowledge base selector.
  await expect(page.locator('#ingfocus, #reingfocus, #regenfocus, #ingtheme, #reingtheme, #regentheme')).toHaveCount(0);

  await ingestModal.locator('#btn-ingest-create-kb').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg).toBeVisible();
  // The dialog opens for the type "fictional" and shows the shared knowledge bases of that type.
  await expect(dlg.locator('input[name="knowledge_type"][value="fictional"]')).toBeChecked();
  await expect(dlg.locator('#claire-similar-kbs')).toContainText('Fantasy Lore');
  // A shared knowledge base of another server has the domain of its server and the link to its origin.
  const remoteKb = dlg.locator('#claire-similar-kbs a[data-similar-remote="b.example"]');
  await expect(remoteKb).toContainText('Dragon Archive');
  await expect(remoteKb).toHaveAttribute('href', 'https://b.example/kb/900/');
  await expect(dlg.locator('#newkb-model')).toHaveCount(0);

  // More than 20 seed entities: the field is not valid and no request goes out.
  await dlg.locator('#newkb-label').fill('Validation Test');
  // The text of the form goes to the server, which gives the knowledge bases near the subject.
  await expect.poll(() => calls.some((c) => c.path.startsWith('/api/kb/similar?type=fictional&label=Validation%20Test'))).toBe(true);
  await dlg.locator('#newkb-seeds').fill(Array.from({ length: 21 }, (_, i) => 'seed' + i).join(', '));
  await dlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect(dlg.locator('#newkb-seeds')).toHaveAttribute('aria-invalid', 'true');
  await expect(dlg).toBeVisible();
});

test('/settings/kb: five tabs, the dry run before the rule turns on, reputation, mutual collaboration; /admin/kb and /admin/themes', async ({ page }) => {
  await authenticateOwner(page, 2);
  const calls = await installApi(page);

  await page.goto('/settings/kb', { waitUntil: 'domcontentloaded' });
  const tabs = page.locator('#settings-kb-tabs [role="tab"]');
  await expect(tabs).toHaveCount(5);
  await expect(page.locator('#settings-kb-host')).toContainText('Personal Research');
  await expect(page.locator('#settings-kb-host a[href="/settings/kb/101"]')).toBeVisible();

  // Submissions: the theme and the state of each target.
  await tabs.nth(1).click();
  await expect(page.locator('#settings-kb-host')).toContainText('Railway Timetable 1851');
  await expect(page.locator('#settings-kb-host [data-withdraw-c="9002"]')).toBeVisible();
  // The contributions of the account in all shared knowledge bases (GET /api/contributions): the withdraw action only for a contribution that is not approved.
  await expect(page.locator('#settings-kb-host')).toContainText('Canal Survey 1840');
  await expect(page.locator('#settings-kb-host [data-withdraw-c="9003"]')).toBeVisible();
  await expect(page.locator('#settings-kb-host [data-withdraw-c="9005"]')).toHaveCount(0);
  expect(calls.some((c) => c.path === '/api/contributions?origin=auto')).toBe(true);

  // Automatic rule (design Section 9.9 rule 6): the dry run shows first; the rule turns on after the confirmation, with each field of the body.
  await tabs.nth(2).click();
  // The list of automatic contributions has the target on another server with its server, its state, and the reason of the other server; it has no withdrawal (federation design Section 12.3).
  const autoRow = page.locator('#settings-kb-host tr', { hasText: 'Steam Engine Notes' });
  await expect(autoRow).toContainText('Railway Archive');
  await expect(autoRow).toContainText('b.example');
  await expect(autoRow).toContainText('다른 서버가 지금 자동 기여를 받지 않음');
  await expect(autoRow.locator('a[href="https://b.example/kb/901/"]')).toBeVisible();
  await expect(autoRow.locator('[data-withdraw-c]')).toHaveCount(0);
  await page.locator('#rule-memberships').check();
  await page.locator('input[name="rule-theme"][value="201"]').check();
  await page.locator('#kb-rule-activate').click();
  const dryDlg = page.locator('dialog.claire-dlg[open]');
  await expect(dryDlg).toContainText('Modern History');
  await expect(dryDlg).toContainText('Railway Timetable 1851');
  // The targets on other servers: the label with the server and a link to the page on that server; a "closed" target has its note and no document.
  await expect(dryDlg.locator('a[href="https://b.example/kb/901/"]')).toContainText('Railway Archive (b.example)');
  await expect(dryDlg).toContainText('Steam Locomotive Log');
  const closedRow = dryDlg.locator('tr', { hasText: 'Canal Archive (c.example)' });
  await expect(closedRow).toContainText('닫힘');
  await expect(closedRow).toContainText('자동 기여를 받지 않습니다');
  expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/auto-rule')).toBe(false);
  await dryDlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect.poll(() => calls.find((c) => c.method === 'PUT' && c.path === '/api/auto-rule')?.body).toEqual({ state: 'active', memberships: true, mutual: false, include_uploads: false, themes: ['201'] });
  await expect(page.locator('#kb-rule-pause')).toBeVisible();

  // Reputation: the fiction-like documents with the action "Contribute to this theme" (POST /submissions with the document and the theme).
  await page.locator('#settings-kb-tabs [role="tab"]').nth(3).click();
  // The limits of GET /api/reputation: 5 pending contributions; the automatic contribution is paused (0).
  await expect(page.locator('#settings-kb-host')).toContainText('대기 중인 기여 한도');
  await expect(page.locator('#settings-kb-host')).toContainText('멈춤');
  await page.locator('[data-fic-contrib="doc-2"]').click();
  const contribDlg = page.locator('dialog.claire-dlg[open]');
  await contribDlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect.poll(() => calls.find((c) => c.method === 'POST' && c.path === '/submissions')?.body).toEqual({ documents: ['doc-2'], theme: '202' });
  await page.keyboard.press('Escape');

  // Memberships: a join request that waits shows after a reload ("membership_state"). Mutual collaboration: a request of another account has the action "accept".
  await page.locator('#settings-kb-tabs [role="tab"]').nth(4).click();
  await expect(page.locator('#settings-kb-host [data-leave-kb="102"]')).toBeVisible();
  await expect(page.locator('#settings-kb-host [data-mut-accept="5550001"]')).toBeVisible();

  // /admin/kb: the filter "failures and warnings" keeps the knowledge base without an active moderator and the one with a failed merge.
  await page.goto('/admin/kb', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#admin-kb-host')).toContainText('Fantasy Lore');
  await page.locator('#admin-kb-host input[name="admin-kb-filter"][value="warn"]').locator('..').click();
  await expect(page.locator('#admin-kb-host tbody tr')).toHaveCount(2);
  await page.locator('#admin-kb-host .claire-menu-trigger').first().click();
  await page.locator('[data-menu-action="delete_kb"]').first().click();
  const delDlg = page.locator('dialog.claire-dlg[open]');
  await expect(delDlg).toContainText('120');
  // The plan request has no "apply": nothing is deleted before the confirmation.
  expect(calls.some((c) => c.method === 'DELETE' && c.path.includes('apply=true'))).toBe(false);
  await page.keyboard.press('Escape');

  // /admin/themes: thresholds, adjustments, agreement of decisions, reputations.
  await page.goto('/admin/themes', { waitUntil: 'domcontentloaded' });
  const adminTabs = page.locator('#admin-themes-tabs [role="tab"]');
  await expect(adminTabs).toHaveCount(4);
  await expect(page.locator('#th-tm')).toHaveValue('0.25');
  await expect(page.locator('#th-ar')).toHaveValue('50');
  await expect(page.locator('#th-sx')).toHaveValue('20');
  await adminTabs.nth(1).click();
  // A member of another server (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.2): the link to its origin and the domain of its server.
  const remoteMember = page.locator('#admin-themes-host a[href="https://b.example/kb/901/"]');
  await expect(remoteMember).toHaveText('Railway Archive');
  await expect(page.locator('#admin-themes-host')).toContainText('b.example');
  await page.locator('#adm-th-merge').click();
  // A theme merges only into a theme of the same knowledge type.
  await expect(page.locator('dialog.claire-dlg[open] #merge-into option')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await adminTabs.nth(2).click();
  await expect(page.locator('#admin-themes-host')).toContainText('75%');
});

test('/settings/kb/{number}: the data of one knowledge base, the titles of the contributions, and the moderation log for a moderator', async ({ page }) => {
  await authenticateOwner(page, 3);
  const calls = await installApi(page);

  await page.goto('/settings/kb/101', { waitUntil: 'domcontentloaded' });
  const tabs = page.locator('#kb-manage-tabs [role="tab"]');
  // Queue, contributions, curation; for a moderator also members and roles, settings, moderation log.
  await expect(tabs).toHaveCount(6);
  expect(calls.some((c) => c.method === 'GET' && c.path === '/kb/101/')).toBe(true);
  expect(calls.some((c) => c.path === '/kb/101/documents')).toBe(false);
  await tabs.nth(5).click();
  await expect(page.locator('#kb-log-host')).toContainText('기여 거절');
  await expect(page.locator('#kb-log-host')).toContainText('Bob');
  await expect(page.locator('#kb-log-host')).toContainText('9004');
});
