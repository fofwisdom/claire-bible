const { test, expect } = require('@playwright/test');

test.use({ serviceWorkers: 'block' });

async function authenticateOwner(page, n) {
  await page.goto('/?t=e2e-sharedkb' + n + '-token-0123456789abc', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
}

async function installSharedKbMocks(page) {
  let personalPublic = false;
  let autoRuleState = 'paused';
  const kbs = [
    {
      number: '10',
      label: 'Personal Research',
      is_personal: true,
      is_public: personalPublic,
      knowledge_type: 'real',
      stats: { documents: 12, entities: 44, relations: 30 }
    },
    {
      number: '101',
      label: 'Modern History Shared KB',
      description: 'Collaborative world history notes',
      knowledge_type: 'real',
      join_policy: 'approval',
      role: 'moderator',
      active_moderators: 1,
      stats: { documents: 18, entities: 75, relations: 52 },
      seed_entities: ['Industrial Revolution', 'Treaty of Versailles'],
      theme_number: '201',
      theme_label: 'Modern History'
    },
    {
      number: '102',
      label: 'Fantasy Lore Archive',
      description: 'Fictional universe compendium',
      knowledge_type: 'fictional',
      join_policy: 'open',
      role: 'member',
      active_moderators: 0,
      reembed_required: true,
      merge_failures: 2,
      embedding_model: 'text-embedding-3-large',
      stats: { documents: 9, entities: 30, relations: 18 }
    },
    {
      number: '202',
      label: 'Remote Astrophysics Collective',
      origin: 'https://astro.example.org/kb/202',
      origin_url: 'https://astro.example.org/kb/202',
      actor_iri: 'https://astro.example.org/actors/kb-202',
      knowledge_type: 'real',
      theme_number: '201',
      theme_label: 'Modern History',
      follow_state: 'following',
      stats: { documents: 64, entities: 210 }
    }
  ];

  await page.route((u) => u.pathname === '/kb' || u.pathname.startsWith('/kb/'), async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const accept = (req.headers()['accept'] || '').toLowerCase();
    const isJson = req.method() !== 'GET' || accept.includes('application/json');
    if (!isJson) {
      await route.continue();
      return;
    }
    if (url.pathname === '/kb' && req.method() === 'GET') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ orderedItems: kbs, personal_kb: kbs[0] })
      });
      return;
    }
    if (url.pathname === '/kb' && req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}');
      const created = {
        number: '103',
        label: body.label || 'New Shared KB',
        knowledge_type: body.knowledge_type || 'real',
        join_policy: 'open',
        role: 'moderator',
        stats: { documents: 0, entities: 0 }
      };
      kbs.push(created);
      await route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({ ok: true, knowledge_base: created }) });
      return;
    }
    if (/^\/kb\/\d+\/?$/.test(url.pathname)) {
      const num = url.pathname.replace(/\D+/g, '');
      const kb = kbs.find((x) => x.number === num) || kbs[1];
      if (req.method() === 'PATCH') {
        const patch = JSON.parse(req.postData() || '{}');
        if (patch.is_public !== undefined) {
          personalPublic = !!patch.is_public;
          kb.is_public = personalPublic;
        }
        Object.assign(kb, patch);
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, knowledge_base: kb }) });
        return;
      }
      if (req.method() === 'DELETE') {
        if (url.searchParams.get('apply') === 'true') {
          await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, applied: true }) });
        } else {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ ok: true, applied: false, would_delete: { documents: 9, entities: 30, relations: 18, members: 4, contributions: 9 } })
          });
        }
        return;
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, knowledge_base: kb }) });
      return;
    }
    if (url.pathname.endsWith('/contributions')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          contributions: [
            {
              number: '501',
              document: 'doc-1',
              title: 'Railway Timetable 1851',
              origin: 'local',
              state: 'pending',
              contributor: { account: 'alice', display_name: 'Alice' },
              contributor_reputation: { level: 'good', approval_rate: 0.95 },
              review_help: { routed_by: 'theme', theme_score: 0.84, other_decisions: [{ knowledge_base: '102', label: 'Other KB', decision: 'approved', reason: '' }] }
            },
            {
              number: '502',
              document: 'doc-2',
              title: 'Steam Engine Patent Notes',
              origin: 'remote',
              state: 'approved',
              contributor: { account: 'bob', display_name: 'Bob' }
            }
          ]
        })
      });
      return;
    }
    if (url.pathname.endsWith('/members')) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          members: [
            { number: '1', account: 'op', display_name: 'Operator', role: 'moderator', state: 'active' },
            { number: '2', account: '@carol@remote.org', display_name: 'Carol Remote', role: 'collaborator', state: 'active', grant_expires_at: '2027-05-01' }
          ]
        })
      });
      return;
    }
    if (url.pathname.endsWith('/moderation-log')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ logs: [{ actor_display_name: 'Operator', action: 'approve', target: 'doc-2', reason: 'on_topic', created_at: '2026-11-17' }] }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
  });

  await page.route((u) => u.pathname === '/submissions' || u.pathname.startsWith('/submissions/'), async (route) => {
    const req = route.request();
    if (req.method() === 'POST') {
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, submission: { number: '900', items: [{ document: 'doc-1', theme_label: 'Modern History', targets: [{ knowledge_base: '101', label: 'Modern History Shared KB', state: 'pending', contribution: '501' }] }] } })
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        orderedItems: [
          {
            number: '900',
            items: [
              { document: 'doc-1', title: 'Railway Timetable 1851', theme: '201', theme_label: 'Modern History', targets: [{ knowledge_base: '101', label: 'Modern History Shared KB', state: 'pending', approved: false, contribution: '501' }] }
            ]
          }
        ]
      })
    });
  });

  await page.route((u) => u.pathname === '/api/auto-rule' || u.pathname.startsWith('/api/auto-rule/'), async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.pathname === '/api/auto-rule/dry-run') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ server_switch: true, targets: [{ knowledge_base: '101', label: 'Modern History Shared KB', mode: 'theme', documents: ['doc-1', 'doc-2'] }] })
      });
      return;
    }
    if (req.method() === 'PUT') {
      const body = JSON.parse(req.postData() || '{}');
      autoRuleState = body.state || autoRuleState;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ server_switch: true, rule: { state: autoRuleState, memberships: true, mutual: false, include_uploads: false, themes: [{ number: '201', label: 'Modern History' }] } })
    });
  });

  await page.route('**/api/reputation', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        level: 'good',
        fiction_share: 0.12,
        approval_rate: 0.94,
        pending_limit: 20,
        documents: [{ document: 'doc-fic-1', title: 'Space Opera Draft', theme: '202', theme_label: 'Space Fiction' }]
      })
    });
  });

  await page.route('**/api/mutual-collaborations**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ mutual_collaborations: [{ account: 'bob', display_name: 'Bob', state: 'pending', requested_by_me: false }] })
    });
  });

  await page.route((u) => u.pathname === '/themes' || u.pathname.startsWith('/themes/'), async (route) => {
    const req = route.request();
    const accept = (req.headers()['accept'] || '').toLowerCase();
    if (!accept.includes('application/json')) {
      await route.continue();
      return;
    }
    const url = new URL(req.url());
    if (/^\/themes\/\d+\/?$/.test(url.pathname)) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          number: '201',
          label: 'Modern History',
          description: '19th-20th century infrastructure and diplomacy',
          knowledge_type: 'real',
          top_entities: ['Railways', 'Treaty of Versailles', 'Steam Power'],
          knowledge_bases: [
            { number: '101', label: 'Modern History Shared KB', is_personal: false, knowledge_type: 'real' },
            { number: '10', label: 'Personal Research', is_personal: true, knowledge_type: 'real', limited: true }
          ]
        })
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        orderedItems: [
          { number: '201', label: 'Modern History', description: 'History cluster', knowledge_type: 'real', shared_knowledge_bases: 1, visible_contributions: 18, top_entities: ['Railways', 'Steam'] },
          { number: '202', label: 'Space Fiction', description: 'Sci-fi worldbuilding', knowledge_type: 'fictional', shared_knowledge_bases: 1, visible_contributions: 9, top_entities: ['Starship'] }
        ]
      })
    });
  });

  await page.route((u) => u.pathname === '/api/admin/kb' || u.pathname.startsWith('/api/admin/kb/'), async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ knowledge_bases: kbs.filter((k) => !k.is_personal) })
    });
  });

  await page.route((u) => u.pathname === '/api/admin/themes' || u.pathname.startsWith('/api/admin/themes/'), async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/admin/themes/agreement') {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items: [{ document: 'doc-1', title: 'Railway Timetable 1851', agreement: 1, decisions: [{ knowledge_base: '101', label: 'Modern History Shared KB', decision: 'approve' }] }] })
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        themes: [
          { number: '201', label: 'Modern History', knowledge_type: 'real', label_locked: false, knowledge_bases: [{ number: '101', label: 'Modern History Shared KB', membership: 'computed' }] },
          { number: '202', label: 'Space Fiction', knowledge_type: 'fictional', label_locked: true, knowledge_bases: [{ number: '102', label: 'Fantasy Lore Archive', membership: 'pinned' }] }
        ],
        settings: { theme_match_threshold: 0.65, document_match_threshold: 0.6, seed_match_threshold: 0.7, auto_contribution_min_score: 0.75, last_judged_at: '2026-11-17T03:00Z' }
      })
    });
  });

  await page.route('**/api/admin/reputations**', async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ summary: { good: 5, limited: 1, low: 0 } }) });
  });

  await page.route('**/api/admin/federation/known-servers**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ servers: [{ domain: 'astro.example.org', status: 'active', kb_count: 1, kbs: [{ number: '202', label: 'Remote Astrophysics Collective', knowledge_type: 'real', documents: 64 }] }] })
    });
  });

  await page.route('**/api/admin/federation/journal**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ entries: [{ id: 'j1', activity_type: 'Create', actor: '@alice@astro.example.org', status: 'failed', received_at: '2026-11-17T02:00Z' }] })
    });
  });
}

test('workspace CT28 focus seal, standing fiction notice, and Create KB dialog validation', async ({ page }) => {
  await authenticateOwner(page, 1);
  await installSharedKbMocks(page);

  await page.evaluate(() => {
    if (typeof setCenterView === 'function') setCenterView('graph');
    if (document.body.classList.contains('ctx-closed') && typeof toggleContextPanel === 'function') toggleContextPanel();
  });
  await expect(page.locator('#ctx-personal-kb-card a[href="/settings/kb"]')).toBeVisible();

  await page.locator('#addbtn').click();
  await page.locator('#pop-ingest-menu [data-ingest-cmd="ingest"]').click();
  const ingestModal = page.locator('#modal-ingest');
  await expect(ingestModal).toBeVisible();
  await expect(ingestModal.locator('#ingest-fiction-notice')).toBeVisible();
  await expect(ingestModal.locator('#ingfocus')).toHaveCount(0);

  // Clicking "Create Shared KB" from ingest notice opens native Create KB dialog
  await ingestModal.locator('#btn-ingest-create-kb').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg).toBeVisible();
  await expect(dlg.locator('#claire-similar-kbs')).toContainText('Modern History Shared KB');

  // Seed entity > 20 triggers accessible :user-invalid validation state
  await dlg.locator('#newkb-label').fill('Validation Test KB');
  const twentyOne = Array.from({ length: 21 }, (_, i) => 'seed' + i).join(', ');
  await dlg.locator('#newkb-seeds').fill(twentyOne);
  await dlg.getByRole('button', { name: /Create Shared KB|공유 지식베이스 만들기/ }).click();
  await expect(dlg.locator('#newkb-seeds')).toHaveAttribute('aria-invalid', 'true');
  await expect(dlg.locator('#newkb-seeds-err')).toBeVisible();
  await dlg.getByRole('button', { name: /Cancel|취소/ }).click();
  await expect(dlg).toHaveCount(0);
});

test('settings/kb 5-tab workflow, /settings/kb/{number} moderation console, and /admin/kb + /admin/themes', async ({ page }) => {
  await authenticateOwner(page, 2);
  await installSharedKbMocks(page);

  await page.goto('/settings/kb', { waitUntil: 'domcontentloaded' });
  const tabs = page.locator('#settings-kb-tabs [role="tab"]');
  await expect(tabs).toHaveCount(5);

  // Dry-run dialog under auto-contribution tab
  await tabs.nth(2).click();
  await page.locator('#kb-rule-dry').click();
  const dryDlg = page.locator('dialog.claire-dlg[open]');
  await expect(dryDlg).toContainText('Modern History Shared KB');
  await dryDlg.locator('.claire-dlg-foot button').last().click();

  // Reputation & mutual tabs
  await tabs.nth(3).click();
  await expect(page.locator('#settings-kb-host')).toContainText('Space Opera Draft');
  await tabs.nth(4).click();
  await expect(page.locator('#settings-kb-host')).toContainText('Bob');

  // Review & moderation console (/settings/kb/101)
  await page.goto('/settings/kb/101', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#rkb')).toBeVisible();
  await expect(page.locator('#settings-kb-manage-host')).toContainText('Railway Timetable 1851');
  await expect(page.locator('#settings-kb-manage-host')).toContainText('Other KB');

  // Admin KB filter chips + row popover menu + Delete dry-run dialog (autofocus Cancel)
  await page.goto('/admin/kb', { waitUntil: 'domcontentloaded' });
  await page.locator('#admin-kb-host input[name="admin-kb-filter"][value="nomod"]').locator('..').click();
  await expect(page.locator('#admin-kb-host')).toContainText('Fantasy Lore Archive');
  await expect(page.locator('#admin-kb-host')).not.toContainText('Modern History Shared KB');

  const rowMenuBtn = page.locator('#admin-kb-host .claire-menu-trigger').first();
  await rowMenuBtn.click();
  await expect(rowMenuBtn).toHaveAttribute('aria-expanded', 'true');
  await page.locator('[data-menu-action="delete_kb"]').first().click();
  const delDlg = page.locator('dialog.claire-dlg[open]');
  await expect(delDlg).toContainText('30');
  const cancelBtn = delDlg.getByRole('button', { name: /Cancel|취소/ });
  await expect(cancelBtn).toBeFocused();
  await cancelBtn.click();

  // Admin themes 3 tabs + same-type merge dialog
  await page.goto('/admin/themes', { waitUntil: 'domcontentloaded' });
  await page.locator('#admin-themes-tabs [role="tab"]').nth(1).click();
  await page.locator('#adm-th-merge').click();
  await expect(page.locator('dialog.claire-dlg[open]')).toBeVisible();
  await page.keyboard.press('Escape');
});

test('public /kb, /kb/{number}/, /explore/themes/{number}, mobile viewport overflow, and unmocked 404 fallback', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));

  // Live server without shared-kb backend mocks: degrades gracefully with notice & 0 page errors
  await authenticateOwner(page, 3);
  await page.goto('/settings/kb', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#settings-kb-host [role="tabpanel"]:not([hidden]) .claire-note.warn')).toBeVisible();

  await installSharedKbMocks(page);
  await page.goto('/kb/101/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#public-kb-root')).toContainText('kb_ingest_by_contribution');
  await page.locator('#public-kb-root details summary').click();
  await expect(page.locator('#public-kb-root')).toContainText('403');
  const overflowKb = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflowKb).toBeLessThanOrEqual(2);

  await page.goto('/kb/202/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#public-kb-root')).toContainText('https://astro.example.org/kb/202');

  await page.goto('/explore/themes/201', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#public-kb-root')).toContainText('Modern History');
  const overflowTheme = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflowTheme).toBeLessThanOrEqual(2);

  expect(errors).toEqual([]);
});
