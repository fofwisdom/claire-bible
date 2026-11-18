const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys, kbItem, contribution, installJsonApi } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /admin/themes and /admin/kb against answers in the shapes of api/server.py: "_admin_themes_payload" ("_theme_view" with "admin"), "admin_reputations_route" (the keys of reputation.SETTINGS), "admin_themes_judge_route" (the fields of theme_judgement.RunReport), "admin_auto_contribution_route", and "_kb_collection_item".

const REP_SETTINGS = { reputation_fiction_share_limited: 0.2, reputation_fiction_share_low: 0.5, reputation_approval_rate_limited: 0.5, reputation_approval_rate_low: 0.2, reputation_window_days: 180, reputation_min_decisions: 10, pending_limit_good: 100, pending_limit_limited: 20, pending_limit_low: 5, auto_contribution_limited_documents: 10 };

// "settings" of GET /api/admin/themes: theme_judgement.SETTINGS and contributions.LIMITS (the defaults).
const THEME_SETTINGS = { theme_match_threshold: 0.25, document_match_threshold: 0.5, seed_match_threshold: 0.5, submission_max_documents: 20, remote_account_pending_limit: 20, remote_domain_pending_limit: 200, remote_domain_daily_offers: 500, remote_domain_daily_activities: 10000, remote_domain_daily_bytes: 104857600, kb_followers_limit: 1000, remote_domain_followers_limit: 1000, remote_revision_min_seconds: 300, remote_domain_daily_revisions: 200 };

function adminTheme(number, label, over) {
  return { number: String(number), url: 'http://127.0.0.1:8766/themes/' + number + '/', label, description: '', knowledge_type: 'real', state: 'active', top_entities: [], shared_knowledge_bases: 0, remote_knowledge_bases: 0, visible_contributions: 0, last_activity_at: 1700000000, knowledge_bases: [], label_locked: false, computed_at: 1700000000, ...over };
}

function member(over) {
  return { number: null, label: '', is_personal: false, knowledge_type: 'real', url: null, remote: false, server: null, pinned: false, excluded: false, source: 'computed', score: 0.5, ...over };
}

test('/admin/themes: the reputation settings form with its labels, the labels of the run report, and a merged theme without the membership menu', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'admthemes1');
  const repSettings = { ...REP_SETTINGS };
  const themeSettings = { ...THEME_SETTINGS };
  const themes = [
    adminTheme(201, 'Railway · Steam', { shared_knowledge_bases: 2, remote_knowledge_bases: 1, knowledge_bases: [
      member({ number: '101', label: 'Modern History', url: 'http://127.0.0.1:8766/kb/101/', score: 0.6 }),
      member({ number: '104', label: 'Steam Engines', url: 'http://127.0.0.1:8766/kb/104/', pinned: true, source: 'pinned', score: null }),
      member({ label: 'Railway Archive', url: 'https://b.example/kb/901/', remote: true, server: 'b.example', remote_id: 5, score: 0.4 })
    ] }),
    adminTheme(203, 'Canals', { state: 'merged', knowledge_bases: [member({ number: '105', label: 'Canal Records', url: 'http://127.0.0.1:8766/kb/105/', score: 0.3 })] }),
    adminTheme(204, 'Empty Subject', { state: 'empty' })
  ];
  const { calls, unknown } = await installJsonApi(page, (u) => u.pathname.startsWith('/api/admin/themes') || u.pathname.startsWith('/api/admin/reputations') || u.pathname === '/api/admin/auto-contribution', ({ path, method, body }) => {
    if (path === '/api/admin/themes' && method === 'GET') return [{ settings: themeSettings, last_run_at: 1700000000, themes, decision_agreement: [{ knowledge_base: '101', label: 'Modern History', decisions: 12, agreement: 0.75 }] }];
    // PUT /api/admin/themes/settings (api/server.py, "admin_themes_settings_route"): {"ok", "settings"} with all keys.
    if (path === '/api/admin/themes/settings' && method === 'PUT') {
      Object.assign(themeSettings, body);
      return [{ ok: true, settings: themeSettings }];
    }
    if (path === '/api/admin/auto-contribution' && method === 'PUT') return [{ ok: true, state: 'on', settings: { auto_match_threshold: 0.5, auto_run_documents: 50, ...body } }];
    if (path === '/api/admin/reputations' && method === 'GET') return [{ settings: repSettings, accounts: [
      { account: '5550001', display_name: 'Bob', level: 'good', fiction_share: 0, fiction_like_documents: 0, visible_documents: 3, approval_rate: 0.9, decisions: 10 },
      { account: '5550002', display_name: 'Carol', level: 'limited', fiction_share: 0.25, fiction_like_documents: 1, visible_documents: 4, approval_rate: null, decisions: 0 },
      { account: '5550003', display_name: 'Dan', level: 'low', fiction_share: 0.6, fiction_like_documents: 3, visible_documents: 5, approval_rate: 0.1, decisions: 20 }
    ] }];
    if (path === '/api/admin/reputations/settings' && method === 'PUT') {
      Object.assign(repSettings, body);
      return [{ ok: true, settings: repSettings }];
    }
    if (path === '/api/admin/auto-contribution' && method === 'GET') return [{ ok: true, state: 'on', settings: { auto_match_threshold: 0.5, auto_run_documents: 50 } }];
    if (path === '/api/admin/themes/judge' && method === 'POST') return [{ ok: true, report: { signatures: 3, themes_active: 2, themes_new: 1, themes_empty: 1, memberships: 4, remote_memberships: 1, document_matches: 17 } }];
    if (/^\/api\/admin\/themes\/201\/(remote-)?knowledge-bases\/\d+$/.test(path) && method === 'PUT') return [{ ok: true, theme: themes[0] }];
    return null;
  });
  await page.goto('/admin/themes', { waitUntil: 'domcontentloaded' });
  const host = page.locator('#admin-themes-host');
  const tabs = page.locator('#admin-themes-tabs [role="tab"]');
  await expect(tabs).toHaveText(['판정과 임계값', '조정', '결정 일치도', '평판']);

  // The judgement tab: the labels of the number fields ("theme_judgement.SETTINGS", "contributions.LIMITS", and the two settings of the automatic contribution), and "Judge now". The report shows each field with its label, not with its key.
  const judge = page.locator('#admin-themes-tabs [role="tabpanel"][data-tabpanel="judge"]');
  await expect(judge.locator('#adm-th-thresh-form label')).toHaveCount(15);
  await expect(judge.locator('#th-rd')).toHaveValue('200');
  for (const label of await judge.locator('#adm-th-thresh-form label').allTextContents()) expect(label.trim().length).toBeGreaterThan(3);
  // Each key of contributions.LIMITS has a field with its label (the key in brackets at the end), its value, and the lowest value that the server takes.
  const limitFields = { submission_max_documents: 'th-sx', remote_account_pending_limit: 'th-ra', remote_domain_pending_limit: 'th-rd', remote_domain_daily_offers: 'th-ro', remote_domain_daily_activities: 'th-da', remote_domain_daily_bytes: 'th-db', kb_followers_limit: 'th-kf', remote_domain_followers_limit: 'th-df', remote_revision_min_seconds: 'th-rm', remote_domain_daily_revisions: 'th-dv' };
  for (const [key, id] of Object.entries(limitFields)) {
    const label = (await judge.locator('label[for="' + id + '"]').textContent()).trim();
    expect(label.endsWith('(' + key + ')'), 'label of ' + key).toBe(true);
    expect(label, 'label of ' + key).not.toBe('(' + key + ')');
    await expect(judge.locator('#' + id)).toHaveValue(String(THEME_SETTINGS[key]));
    await expect(judge.locator('#' + id)).toHaveAttribute('min', key === 'remote_revision_min_seconds' ? '0' : '1');
  }
  await expect(judge.locator('label[for="th-kf"]')).toHaveText('공유 지식 베이스 하나의 팔로워 수 (kb_followers_limit)');
  // Save: a filled field goes in the body of PUT /api/admin/themes/settings; an empty field is not sent. 0 is a valid minimum time of a revision.
  await judge.locator('#th-db').fill('');
  await judge.locator('#th-rm').fill('0');
  await judge.locator('#th-kf').fill('500');
  await judge.locator('#adm-th-thresh-form button[type="submit"]').click();
  await expect(page.locator('.claire-toast').last()).toBeVisible();
  const expected = { ...THEME_SETTINGS, remote_revision_min_seconds: 0, kb_followers_limit: 500 };
  delete expected.remote_domain_daily_bytes;
  await expect.poll(() => calls.filter((c) => c.method === 'PUT' && c.path === '/api/admin/themes/settings').map((c) => c.body)).toEqual([expected]);
  expect(calls.filter((c) => c.method === 'PUT' && c.path === '/api/admin/auto-contribution').map((c) => c.body)).toEqual([{ auto_match_threshold: 0.5, auto_run_documents: 50 }]);
  // The page reads again: the fields show the stored values.
  await expect(judge.locator('#th-rm')).toHaveValue('0');
  await expect(judge.locator('#th-kf')).toHaveValue('500');
  await expect(judge.locator('#th-db')).toHaveValue('104857600');
  await expect(judge.locator('.claire-kpi').nth(1)).toContainText('1 / 1 / 1');
  await judge.locator('#adm-th-run-now').click();
  const report = judge.locator('.claire-note.ok');
  await expect(report).toContainText('판정 실행이 끝났습니다.');
  await expect(report).toContainText('시그니처가 있는 지식 베이스: 3');
  await expect(report).toContainText('활성 테마: 2');
  await expect(report).toContainText('새 테마: 1');
  await expect(report).toContainText('빈 테마: 1');
  await expect(report).toContainText('소속: 4');
  await expect(report).toContainText('다른 서버 지식 베이스의 소속: 1');
  await expect(report).toContainText('테마에 맞은 문서: 17');
  for (const key of ['signatures', 'themes_active', 'themes_new', 'themes_empty', 'remote_memberships', 'document_matches']) await expect(report).not.toContainText(key);
  expect(calls.find((c) => c.method === 'POST' && c.path === '/api/admin/themes/judge').body).toEqual({});
  await expectNoRawKeys(page);

  // The reputation tab: one field for each setting, each with a label text. Only a changed value goes to the server.
  await tabs.nth(3).click();
  const rep = page.locator('#admin-themes-tabs [role="tabpanel"][data-tabpanel="rep"]');
  const repLabels = rep.locator('#adm-rep-form label');
  await expect(repLabels).toHaveCount(10);
  await expect(rep.locator('label[for="rep-reputation_window_days"]')).toHaveText('승인율에 넣는 결정의 기간, 일 (reputation_window_days)');
  await expect(rep.locator('label[for="rep-pending_limit_low"]')).toContainText('심사를 기다리는 기여의 최대 수, 수준 낮음');
  for (const key of Object.keys(REP_SETTINGS)) {
    const label = (await rep.locator('label[for="rep-' + key + '"]').textContent()).trim();
    // A label is a sentence with the key in brackets at its end. A label that is only the key has no catalog text.
    expect(label, 'label of ' + key).not.toBe(key);
    expect(label.endsWith('(' + key + ')'), 'label of ' + key).toBe(true);
    await expect(rep.locator('#rep-' + key)).toHaveValue(String(REP_SETTINGS[key]));
  }
  // No change: no request.
  await rep.locator('#adm-rep-form button[type="submit"]').click();
  await rep.locator('#rep-reputation_window_days').fill('90');
  await rep.locator('#adm-rep-form button[type="submit"]').click();
  await expect(page.locator('.claire-toast').last()).toBeVisible();
  expect(calls.filter((c) => c.method === 'PUT' && c.path === '/api/admin/reputations/settings').map((c) => c.body)).toEqual([{ reputation_window_days: 90 }]);
  // The page reads again and stays on the reputation tab with the new value.
  await expect(tabs.nth(3)).toHaveAttribute('aria-selected', 'true');
  await expect(rep.locator('#rep-reputation_window_days')).toHaveValue('90');
  // The accounts: the level of each account as text.
  const accounts = rep.locator('tbody tr');
  await expect(accounts).toHaveCount(3);
  await expect(accounts.nth(0).locator('.claire-tag')).toHaveClass(/ok/);
  await expect(accounts.nth(1).locator('.claire-tag')).toHaveClass(/warn/);
  await expect(accounts.nth(2).locator('.claire-tag')).toHaveClass(/danger/);
  for (const i of [0, 1, 2]) await expect(accounts.nth(i).locator('.claire-tag')).not.toHaveText(/^(good|limited|low)$/);
  await expect(accounts.nth(2)).toContainText('60%');
  await expectNoRawKeys(page);

  // The adjustments tab: an active theme has the membership menu for each member, the merge control, and the pin form.
  await tabs.nth(1).click();
  const adj = page.locator('#admin-themes-tabs [role="tabpanel"][data-tabpanel="adj"]');
  const picks = adj.locator('[data-pick-th]');
  await expect(picks).toHaveCount(3);
  await expect(picks.nth(0)).toContainText('활동 중');
  await expect(picks.nth(1)).toContainText('병합됨');
  await expect(picks.nth(2)).toContainText('비어 있음');
  await expect(adj.locator('.claire-detail-col tbody tr')).toHaveCount(3);
  await expect(adj.locator('.claire-detail-col .claire-menu-trigger')).toHaveCount(3);
  await expect(adj.locator('#adm-th-merge')).toBeVisible();
  await expect(adj.locator('#adm-th-pin-form')).toBeVisible();
  // The member of another server: its adjustment goes to the route of remote knowledge bases, with the record number.
  await adj.locator('.claire-detail-col tbody tr', { hasText: 'Railway Archive' }).locator('.claire-menu-trigger').click();
  await page.locator('[data-menu-action="th_exclude"][data-menu-value="remote:5"]').click();
  await expect.poll(() => calls.find((c) => c.method === 'PUT' && c.path === '/api/admin/themes/201/remote-knowledge-bases/5')?.body).toEqual({ membership: 'excluded' });
  // A pinned member of this server: the menu has the reset to the computed state.
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
  await adj.locator('.claire-detail-col tbody tr', { hasText: 'Steam Engines' }).locator('.claire-menu-trigger').click();
  await page.locator('[data-menu-action="th_computed"][data-menu-value="104"]').click();
  await expect.poll(() => calls.find((c) => c.method === 'PUT' && c.path === '/api/admin/themes/201/knowledge-bases/104')?.body).toEqual({ membership: 'computed' });

  // A merged theme: its members show, and it has no membership menu, no merge control, and no pin form.
  await expect(picks.nth(1)).toBeVisible();
  await picks.nth(1).click();
  await expect(adj.locator('.claire-detail-col h3')).toHaveText('Canals');
  await expect(adj.locator('.claire-detail-col tbody tr')).toHaveCount(1);
  await expect(adj.locator('.claire-detail-col tbody tr')).toContainText('Canal Records');
  await expect(adj.locator('.claire-detail-col .claire-menu-trigger')).toHaveCount(0);
  await expect(adj.locator('.claire-detail-col [data-menu-action]')).toHaveCount(0);
  await expect(adj.locator('#adm-th-merge')).toHaveCount(0);
  await expect(adj.locator('#adm-th-pin-form')).toHaveCount(0);
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

test('/admin/kb: a knowledge base without an active moderator is first; the warnings, the filter, the retry of failed merges, and the moderator assignment', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'admthemes2');
  // The sequence of the server: 101, 102, 103, 104. The knowledge bases 102 and 104 have no active moderator.
  const kbs = [
    kbItem(101, { label: 'Modern History', active_moderators: 1 }),
    kbItem(102, { label: 'Fantasy Lore', knowledge_type: 'fictional', active_moderators: 0 }),
    kbItem(103, { label: 'Old Maps', active_moderators: 2, reembed_required: true, state: 'archived' }),
    kbItem(104, { label: 'Steam Engines', active_moderators: 0 })
  ];
  let failedLeft = true;
  const { calls, unknown } = await installJsonApi(page, (u) => u.pathname === '/kb' || /^\/kb\/\d+\//.test(u.pathname) || u.pathname.startsWith('/api/admin/kb/'), ({ path, method, query }) => {
    if (path === '/kb' && method === 'GET') return [{ type: 'OrderedCollection', totalItems: kbs.length, knowledge_bases: kbs, personal_kb: null, multi_theme: true }];
    const list = /^\/kb\/(\d+)\/contributions$/.exec(path);
    if (list && method === 'GET' && query.get('state') === 'failed') return [{ type: 'OrderedCollection', totalItems: 0, contributions: list[1] === '101' && failedLeft ? [contribution(9001, { state: 'failed', reason: 'embedding_model_unavailable', approved: false }), contribution(9002, { state: 'failed', reason: 'merge_failed', approved: false })] : [] }];
    const retry = /^\/kb\/101\/contributions\/(\d+)\/retry$/.exec(path);
    if (retry && method === 'POST') {
      failedLeft = false;
      return [{ ok: true, contribution: contribution(Number(retry[1]), { state: 'visible' }) }];
    }
    if (path === '/api/admin/kb/102/moderators/5550001' && method === 'PUT') {
      kbs[1].active_moderators = 1;
      return [{ ok: true }];
    }
    return null;
  });
  await page.goto('/admin/kb', { waitUntil: 'domcontentloaded' });
  const host = page.locator('#admin-kb-host');
  const rows = host.locator('tbody tr');
  await expect(rows).toHaveCount(4);
  // The two without an active moderator come first, in the sequence of the server; then the others in the sequence of the server.
  await expect(rows.nth(0)).toContainText('Fantasy Lore');
  await expect(rows.nth(1)).toContainText('Steam Engines');
  await expect(rows.nth(2)).toContainText('Modern History');
  await expect(rows.nth(3)).toContainText('Old Maps');
  await expect(rows.nth(0).locator('td').nth(2).locator('.claire-tag.warn')).toBeVisible();
  await expect(rows.nth(2).locator('td').nth(2)).toHaveText('1');
  // The failed merges and the mark "re-embed required" show in the state column.
  await expect(rows.nth(2).locator('td').nth(3).locator('.claire-tag.danger')).toContainText('2');
  await expect(rows.nth(3).locator('td').nth(3)).toContainText('보관됨');
  await expect(rows.nth(3).locator('td').nth(3).locator('.claire-tag.danger')).toBeVisible();
  expect(calls.filter((c) => /contributions\?state=failed$/.test(c.path))).toHaveLength(4);
  await expectNoRawKeys(page);

  // The filter "failures and warnings" keeps the two without a moderator, the one with failed merges, and the one that needs a re-embed.
  await host.locator('input[name="admin-kb-filter"][value="warn"]').locator('..').click();
  await expect(rows).toHaveCount(4);
  await expect(rows.nth(0)).toContainText('Fantasy Lore');
  await host.locator('input[name="admin-kb-filter"][value="all"]').locator('..').click();

  // Retry of the failed merges: one POST for each failed contribution; only a knowledge base with failed merges has the action.
  await rows.nth(0).locator('.claire-menu-trigger').click();
  await expect(rows.nth(0).locator('[data-menu-action="retry_merge"]')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await rows.nth(2).locator('.claire-menu-trigger').click();
  await rows.nth(2).locator('[data-menu-action="retry_merge"]').click();
  await expect.poll(() => calls.filter((c) => c.method === 'POST').map((c) => [c.path, c.body])).toEqual([['/kb/101/contributions/9001/retry', {}], ['/kb/101/contributions/9002/retry', {}]]);
  await expect(rows.nth(2).locator('td').nth(3).locator('.claire-tag.danger')).toHaveCount(0);

  // Assign a moderator: PUT with the public number of the account. The knowledge base then leaves the first group.
  await rows.nth(0).locator('.claire-menu-trigger').click();
  await rows.nth(0).locator('[data-menu-action="assign_mod"]').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg.locator('label[for="admin-assign-acc"]')).not.toHaveText('');
  await dlg.locator('#admin-assign-acc').fill('5550001');
  await expectNoRawKeys(page);
  await dlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect(dlg).toHaveCount(0);
  expect(calls.some((c) => c.method === 'PUT' && c.path === '/api/admin/kb/102/moderators/5550001')).toBe(true);
  await expect(rows.nth(0)).toContainText('Steam Engines');
  await expect(rows.nth(1)).toContainText('Modern History');
  await expect(rows.nth(2)).toContainText('Fantasy Lore');
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});
