const { test, expect } = require('@playwright/test');
const { watchErrors, expectNoRawKeys } = require('./support/console_pages');

// The PWA service worker intercepts fetch requests outside page.route(); block it like sibling specs.
test.use({ serviceWorkers: 'block' });

// Single-use login tokens in e2e/seed_db.py rotate on /?t=...; setting the seeded owner session cookie directly avoids token collisions across specs.
const OWNER_COOKIE_TOKEN = 'e2e-owner-token-0123456789abcdef';

function createInitialQueueState() {
  const now = Math.floor(Date.now() / 1000);
  return {
    max_slots: 4,
    busy_slots: 2,
    purge_allowed: true,
    counts: { running: 2, queued: 2, staged: 1, error: 1, done: 1 },
    server_time: now,
    items: [
      {
        id: 'job_9021',
        inbox_id: 9021,
        status: 'running',
        order: null,
        title: 'vLLM PagedAttention Architecture',
        source: 'web',
        kb_id: 0,
        kb_label: 'AI Infra',
        mode: 'provider',
        stage: 'resolve',
        stage_index: 2,
        msg: 'Resolving entities against knowledge graph',
        created_at: now - 40,
        updated_at: now - 10,
        document_id: null,
        doc_url: null,
        can_retry: false,
        can_cancel: true,
        can_purge: false,
        cancel_requested: false,
        claimed_active: false,
        claimed_by: null,
        lease_expires_at: null,
      },
      {
        id: 'job_9022',
        inbox_id: 9022,
        status: 'running',
        order: null,
        title: 'Popover and Anchor Positioning Guide',
        source: 'telegram',
        kb_id: 0,
        kb_label: 'Web Platform',
        mode: 'provider',
        stage: 'extract',
        stage_index: 1,
        msg: 'Extracting claims and entities',
        created_at: now - 25,
        updated_at: now - 5,
        document_id: null,
        doc_url: null,
        can_retry: false,
        can_cancel: true,
        can_purge: false,
        cancel_requested: false,
        claimed_active: false,
        claimed_by: null,
        lease_expires_at: null,
      },
      {
        id: 'inbox_9023',
        inbox_id: 9023,
        status: 'queued',
        order: 1,
        title: 'PostgreSQL 18 Async IO Benchmark',
        source: 'web',
        kb_id: 0,
        kb_label: 'Database',
        mode: 'provider',
        stage: 'fetch',
        stage_index: 0,
        msg: 'Waiting for next free slot',
        created_at: now - 20,
        updated_at: now - 20,
        document_id: null,
        doc_url: null,
        can_retry: false,
        can_cancel: true,
        can_purge: false,
        cancel_requested: false,
        claimed_active: false,
        claimed_by: null,
        lease_expires_at: null,
      },
      {
        id: 'inbox_9024',
        inbox_id: 9024,
        status: 'queued',
        order: 2,
        title: 'Fedify ActivityPub Retry Strategy',
        source: 'feed',
        kb_id: 0,
        kb_label: 'Federation',
        mode: 'provider',
        stage: 'fetch',
        stage_index: 0,
        msg: 'Queued from feed poll',
        created_at: now - 50,
        updated_at: now - 50,
        document_id: null,
        doc_url: null,
        can_retry: false,
        can_cancel: true,
        can_purge: false,
        cancel_requested: false,
        claimed_active: false,
        claimed_by: null,
        lease_expires_at: null,
      },
      {
        id: 'inbox_9025',
        inbox_id: 9025,
        status: 'staged',
        order: null,
        title: 'Private Design Notes for Agent Client',
        source: 'mcp',
        kb_id: 0,
        kb_label: 'Personal KB',
        mode: 'client',
        stage: 'fetch',
        stage_index: 0,
        msg: 'Source stored and waiting for agent client extraction',
        created_at: now - 180,
        updated_at: now - 180,
        document_id: null,
        doc_url: null,
        can_retry: false,
        can_cancel: true,
        can_purge: false,
        cancel_requested: false,
        claimed_active: false,
        claimed_by: null,
        lease_expires_at: null,
      },
      {
        id: 'inbox_501',
        inbox_id: 501,
        status: 'error',
        order: null,
        title: 'https://media.example.org/broken-lecture.mp4',
        source: 'telegram',
        kb_id: 0,
        kb_label: 'Lecture Notes',
        mode: 'provider',
        stage: 'extract',
        stage_index: 1,
        msg: 'video_stt_failed: no captions or audio stream',
        created_at: now - 300,
        updated_at: now - 240,
        document_id: null,
        doc_url: null,
        can_retry: true,
        can_cancel: true,
        can_purge: false,
        cancel_requested: false,
        claimed_active: false,
        claimed_by: null,
        lease_expires_at: null,
      },
      {
        id: 'inbox_500',
        inbox_id: 500,
        status: 'done',
        order: null,
        title: '테스트 문서 1 (핵심 개념)',
        source: 'web',
        kb_id: 0,
        kb_label: '기본',
        mode: 'provider',
        stage: 'done',
        stage_index: 4,
        msg: 'Ingested and committed',
        created_at: now - 600,
        updated_at: now - 540,
        document_id: 'doc-1',
        doc_url: '/silvercord/23456789abcdefgh',
        can_retry: false,
        can_cancel: false,
        can_purge: true,
        cancel_requested: false,
        claimed_active: false,
        claimed_by: null,
        lease_expires_at: null,
      },
    ],
  };
}

function recomputeCounts(state) {
  state.counts = { running: 0, queued: 0, staged: 0, error: 0, done: 0 };
  state.items.forEach((it) => {
    if (Object.prototype.hasOwnProperty.call(state.counts, it.status)) {
      state.counts[it.status] += 1;
    }
  });
  state.busy_slots = state.counts.running;
}

async function installQueueRoutes(page, state, impactMap = {}) {
  const retryCalls = [];
  const cancelCalls = [];
  const jobCancelCalls = [];
  await page.route('**/api/me/ingest-queue', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(state) });
  });
  await page.route('**/api/me/ingest-queue/*/retry', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const url = new URL(route.request().url());
    const match = url.pathname.match(/\/api\/me\/ingest-queue\/(\d+)\/retry$/);
    const inboxId = match ? Number(match[1]) : 0;
    retryCalls.push(inboxId);
    const target = state.items.find((it) => it.inbox_id === inboxId);
    if (!target) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not_found' }) });
    }
    const maxOrder = state.items.reduce((acc, it) => (it.status === 'queued' && typeof it.order === 'number' && it.order > acc ? it.order : acc), 0);
    target.status = 'queued';
    target.order = maxOrder + 1;
    target.stage = 'fetch';
    target.stage_index = 0;
    target.can_retry = false;
    target.can_cancel = true;
    target.msg = 'Re-queued for ingest';
    target.updated_at = Math.floor(Date.now() / 1000);
    recomputeCounts(state);
    return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ ok: true, inbox_id: inboxId, status: 'queued' }) });
  });
  await page.route('**/api/me/ingest-queue/*/cancel', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const url = new URL(route.request().url());
    const match = url.pathname.match(/\/api\/me\/ingest-queue\/(\d+)\/cancel$/);
    const inboxId = match ? Number(match[1]) : 0;
    const body = route.request().postDataJSON() || {};
    cancelCalls.push({ inboxId, body });
    const target = state.items.find((it) => it.inbox_id === inboxId);
    if (!target) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not_found' }) });
    }
    if (body.dry_run) {
      const customImpact = impactMap[inboxId] || {};
      const payload = {
        dry_run: true,
        mode: target.status === 'done' ? 'document_purge' : target.status === 'staged' ? 'staged_cancel' : 'inbox_cancel',
        inbox_id: inboxId,
        document_id: target.document_id,
        purged_count: target.status === 'done' || target.status === 'staged' ? 1 : 0,
        orphan_entities_count: 0,
        shared_entities_count: 0,
        orphan_relations_count: 0,
        disk_files_count: target.status === 'done' || target.status === 'staged' ? 1 : 0,
        federation_objects_count: 0,
        claimed_active: Boolean(target.claimed_active),
        claimed_by: target.claimed_by,
        lease_expires_at: target.lease_expires_at,
        tombstone: Boolean(body.tombstone),
        ...customImpact,
      };
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
    }
    if (target.status === 'staged' && target.claimed_active && !body.force) {
      return route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ error: 'staged_claim_active', claimed_by: target.claimed_by }) });
    }
    state.items = state.items.filter((it) => it.inbox_id !== inboxId);
    let qOrd = 1;
    state.items.filter((it) => it.status === 'queued').forEach((it) => { it.order = qOrd++; });
    recomputeCounts(state);
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, action: target.status === 'done' ? 'purged_document' : 'cancelled', inbox_id: inboxId, tombstone: Boolean(body.tombstone) }) });
  });
  await page.route('**/api/me/ingest-queue/jobs/*/cancel', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    const url = new URL(route.request().url());
    const match = url.pathname.match(/\/api\/me\/ingest-queue\/jobs\/([^/]+)\/cancel$/);
    const jobId = match ? decodeURIComponent(match[1]) : '';
    const body = route.request().postDataJSON() || {};
    jobCancelCalls.push({ jobId, body });
    const target = state.items.find((it) => it.id === jobId);
    if (!target) {
      return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: 'not_found' }) });
    }
    target.cancel_requested = true;
    target.can_cancel = false;
    return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ ok: true, action: 'cancel_requested', job_id: jobId, tombstone: Boolean(body.tombstone) }) });
  });
  return { retryCalls, cancelCalls, jobCancelCalls };
}

async function expectUnifiedSlotMeter(pop, expectedBusy, expectedMax) {
  const meter = pop.locator('#queue-slot-meter');
  await expect(meter).toBeVisible();
  await expect(meter.locator('#queue-slot-label')).toHaveText(new RegExp(`^(?:슬롯|Slots) ${expectedBusy}\\/${expectedMax}$`));
  await expect(meter.locator('.slot-cell')).toHaveCount(expectedMax);
  await expect(meter.locator('.slot-cell.is-busy')).toHaveCount(expectedBusy);
}

async function expectZeroOverflow(page, pop, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, `${label} document horizontal overflow`).toBeLessThanOrEqual(0);
  const popOverflow = await pop.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(popOverflow, `${label} popover horizontal overflow`).toBeLessThanOrEqual(0);
}

test('ingest queue panel supports Option A ledger, Option C compact persistence, retry, themes, and mobile layout', async ({ browser, baseURL }) => {
  const targetOrigin = new URL(baseURL || 'http://127.0.0.1:8766/').origin;
  const context = await browser.newContext({ serviceWorkers: 'block', locale: 'ko-KR' });
  await context.addCookies([{ name: 'claire_session', value: OWNER_COOKIE_TOKEN, url: targetOrigin }]);
  const page = await context.newPage();
  const errors = watchErrors(page);
  const state = createInitialQueueState();
  const { retryCalls } = await installQueueRoutes(page, state);

  await page.setViewportSize({ width: 1360, height: 800 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

  const queueBtn = page.locator('#queuebtn');
  const pop = page.locator('#pop-queue');
  await expect(queueBtn).toBeVisible();
  await expect(page.locator('#notifybtn')).toBeVisible();
  await expect(page.locator('#queue-count')).toHaveText('5');
  await expect(page.locator('#queue-live-dot')).toBeVisible();

  // Verify #queuebtn sits to the right of #notifybtn and to the left of #homelinkbtn.
  const barOrder = await page.evaluate(() => {
    const notify = document.getElementById('notifybtn').getBoundingClientRect();
    const queue = document.getElementById('queuebtn').getBoundingClientRect();
    const home = document.getElementById('homelinkbtn').getBoundingClientRect();
    return { notifyRight: Math.round(notify.right), queueLeft: Math.round(queue.left), queueRight: Math.round(queue.right), homeLeft: Math.round(home.left) };
  });
  expect(barOrder.queueLeft).toBeGreaterThanOrEqual(barOrder.notifyRight);
  expect(barOrder.homeLeft).toBeGreaterThanOrEqual(barOrder.queueRight);

  // Open #pop-queue from #queuebtn and verify Option A (ledger) is the default view.
  await queueBtn.click();
  await expect(pop).toBeVisible();
  await expect.poll(() => pop.evaluate((el) => el.matches(':popover-open'))).toBe(true);
  await expect(pop).toHaveAttribute('data-queue-view', 'ledger');
  await expectUnifiedSlotMeter(pop, 2, 4);
  await expectNoRawKeys(page, '#pop-queue');
  await expect.poll(async () => {
    const b = await pop.boundingBox();
    return b ? Math.round(b.width) : 0;
  }).toBe(480);
  const boxA = await pop.boundingBox();
  expect(boxA).not.toBeNull();

  // Verify #queue-view-toggle is directly left of #queue-close-btn.
  const headLayout = await page.evaluate(() => {
    const toggle = document.getElementById('queue-view-toggle');
    const closeBtn = document.getElementById('queue-close-btn');
    const tRect = toggle.getBoundingClientRect();
    const cRect = closeBtn.getBoundingClientRect();
    return {
      isDirectPrevSibling: toggle.nextElementSibling === closeBtn,
      toggleRight: Math.round(tRect.right),
      closeLeft: Math.round(cRect.left),
      sameRow: Math.abs(tRect.top - cRect.top) < 6,
    };
  });
  expect(headLayout.isDirectPrevSibling).toBe(true);
  expect(headLayout.toggleRight).toBeLessThanOrEqual(headLayout.closeLeft);
  expect(headLayout.sameRow).toBe(true);

  // Verify Option A tablist filtering, #1 queued ordinal pill, <progress> bar, and retry action.
  const tabs = pop.locator('#queue-filter-bar [role="tablist"] [role="tab"]');
  await expect(tabs).toHaveCount(4);
  await expect(tabs.nth(0)).toHaveText(/^(전체|All)/);
  await expect(tabs.nth(1)).toHaveText(/^(진행·대기|Active)/);
  await expect(tabs.nth(2)).toHaveText(/^(에이전트|Agent)/);
  await expect(tabs.nth(3)).toHaveText(/^(완료·오류|History)/);

  await expect(pop.locator('.qa-row.st-queued .qa-order-pill').first()).toHaveText('#1');
  const firstProgress = pop.locator('.qa-row.st-running progress.q-progress').first();
  await expect(firstProgress).toHaveAttribute('max', '5');
  await expect(firstProgress).toHaveAttribute('value', '3');

  await tabs.nth(1).click();
  await expect(tabs.nth(1)).toHaveAttribute('aria-selected', 'true');
  await expect(pop.locator('.qa-row.st-running')).toHaveCount(2);
  await expect(pop.locator('.qa-row.st-queued')).toHaveCount(2);
  await expect(pop.locator('.qa-row.st-staged')).toHaveCount(0);

  await tabs.nth(2).click();
  await expect(pop.locator('.qa-row.st-staged')).toHaveCount(1);
  await expect(pop.locator('.qa-row.st-running')).toHaveCount(0);

  await tabs.nth(3).click();
  await expect(pop.locator('.qa-row.st-error')).toHaveCount(1);
  await expect(pop.locator('.qa-row.st-done')).toHaveCount(1);

  // Retry failed item inbox_501 and verify POST /api/me/ingest-queue/501/retry moves it to queued #3.
  const retryBtn = pop.locator('.qa-row.st-error .q-btn-danger');
  await expect(retryBtn).toBeVisible();
  await retryBtn.click();
  await expect.poll(() => retryCalls.slice()).toEqual([501]);
  await tabs.nth(0).click();
  await expect(pop.locator('.qa-row.st-error')).toHaveCount(0);
  await expect(pop.locator('.qa-row.st-queued')).toHaveCount(3);
  await expect(pop.locator('.qa-row.st-queued .qa-order-pill').nth(2)).toHaveText('#3');

  // Check zero overflow in Light theme Option A before switching to Option C.
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expectZeroOverflow(page, pop, 'desktop light ledger');

  // Switch to Option C (compact) and verify identical width, unified slot meter, dense rows, inline expand, and persistence across reload.
  const viewToggle = page.locator('#queue-view-toggle');
  await viewToggle.click();
  await expect(pop).toHaveAttribute('data-queue-view', 'compact');
  await expect(viewToggle).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(async () => {
    const b = await pop.boundingBox();
    return b ? Math.round(b.width) : 0;
  }).toBe(480);
  const boxC = await pop.boundingBox();
  expect(boxC).not.toBeNull();
  expect(Math.abs(boxA.width - boxC.width)).toBeLessThanOrEqual(1);
  await expectUnifiedSlotMeter(pop, 2, 4);
  await expect(pop.locator('.qc-chips .qc-chip-btn')).toHaveCount(6);
  await expect(pop.locator('.qc-row')).toHaveCount(7);

  const firstCompactRow = pop.locator('.qc-row .qc-row-btn').first();
  await expect(firstCompactRow).toHaveAttribute('aria-expanded', 'false');
  await firstCompactRow.click();
  await expect(firstCompactRow).toHaveAttribute('aria-expanded', 'true');
  await expect(pop.locator('.qc-row .qc-detail').first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('claireQueueViewMode'))).toBe('compact');
  await expectZeroOverflow(page, pop, 'desktop light compact');

  // Reload page and confirm Option C mode persisted from localStorage.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(queueBtn).toBeVisible();
  await queueBtn.click();
  await expect(pop).toBeVisible();
  await expect(pop).toHaveAttribute('data-queue-view', 'compact');
  await expect(viewToggle).toHaveAttribute('aria-pressed', 'true');
  await expectUnifiedSlotMeter(pop, 2, 4);

  // Switch to Dark theme in Option C, then back to Option A in Dark theme, and check zero overflow.
  await page.keyboard.press('Escape');
  await expect.poll(() => pop.evaluate((el) => el.matches(':popover-open'))).toBe(false);
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#pop-hamburger-menu #themebtn')).toBeVisible();
  await page.locator('#pop-hamburger-menu #themebtn').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');

  await queueBtn.click();
  await expect(pop).toBeVisible();
  await expect(pop).toHaveAttribute('data-queue-view', 'compact');
  await expectZeroOverflow(page, pop, 'desktop dark compact');

  await viewToggle.click();
  await expect(pop).toHaveAttribute('data-queue-view', 'ledger');
  await expect(viewToggle).toHaveAttribute('aria-pressed', 'false');
  await expect.poll(() => page.evaluate(() => localStorage.getItem('claireQueueViewMode'))).toBe('ledger');
  await expectZeroOverflow(page, pop, 'desktop dark ledger');

  // Close with #queue-close-btn.
  await page.locator('#queue-close-btn').click();
  await expect.poll(() => pop.evaluate((el) => el.matches(':popover-open'))).toBe(false);

  // Narrow mobile viewport (375x812) in Dark and Light themes must have zero horizontal overflow.
  await page.setViewportSize({ width: 375, height: 812 });
  await queueBtn.click();
  await expect(pop).toBeVisible();
  await expectZeroOverflow(page, pop, 'mobile dark ledger');

  await viewToggle.click();
  await expect(pop).toHaveAttribute('data-queue-view', 'compact');
  await expectZeroOverflow(page, pop, 'mobile dark compact');

  await page.keyboard.press('Escape');
  await expect.poll(() => pop.evaluate((el) => el.matches(':popover-open'))).toBe(false);
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#pop-hamburger-menu #themebtn')).toBeVisible();
  await page.locator('#pop-hamburger-menu #themebtn').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

  await queueBtn.click();
  await expect(pop).toBeVisible();
  await expectZeroOverflow(page, pop, 'mobile light compact');
  await viewToggle.click();
  await expect(pop).toHaveAttribute('data-queue-view', 'ledger');
  await expectZeroOverflow(page, pop, 'mobile light ledger');

  await context.close();
  expect(errors).toEqual([]);
});

test('ingest queue panel supports Stage 1 and Stage 2 cancel and purge flows in Option A and Option C', async ({ browser, baseURL }) => {
  const targetOrigin = new URL(baseURL || 'http://127.0.0.1:8766/').origin;
  const context = await browser.newContext({ serviceWorkers: 'block', locale: 'ko-KR' });
  await context.addCookies([{ name: 'claire_session', value: OWNER_COOKIE_TOKEN, url: targetOrigin }]);
  const page = await context.newPage();
  const errors = watchErrors(page);
  const state = createInitialQueueState();
  const stagedItem = state.items.find((it) => it.inbox_id === 9025);
  stagedItem.claimed_active = true;
  stagedItem.claimed_by = 'antigravity-agent';
  stagedItem.lease_expires_at = Math.floor(Date.now() / 1000) + 300;

  const impactMap = {
    500: {
      purged_count: 1,
      orphan_entities_count: 3,
      shared_entities_count: 2,
      orphan_relations_count: 4,
      disk_files_count: 2,
      federation_objects_count: 1,
    },
  };
  const { cancelCalls, jobCancelCalls } = await installQueueRoutes(page, state, impactMap);

  await page.setViewportSize({ width: 1360, height: 800 });
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

  const queueBtn = page.locator('#queuebtn');
  const pop = page.locator('#pop-queue');
  const modal = page.locator('#modal-queue-cancel');
  await expect(queueBtn).toBeVisible();
  await queueBtn.click();
  await expect(pop).toBeVisible();
  await expect(pop).toHaveAttribute('data-queue-view', 'ledger');

  // 1. Cancel a queued item in Option A: dry_run=true on open, then dry_run=false, tombstone=false on confirm.
  const firstQueuedRow = pop.locator('.qa-row.st-queued').first();
  await firstQueuedRow.locator('[data-queue-action="cancel-inbox"]').click();
  await expect(modal).toBeVisible();
  await expectNoRawKeys(page, '#modal-queue-cancel');

  // #modal-queue-cancel must live outside header.appbar so header.appbar keeps zero <input> elements.
  const headerCheck = await page.evaluate(() => ({
    appbarInputs: document.querySelectorAll('header.appbar input').length,
    modalInsideAppbar: Boolean(document.querySelector('header.appbar #modal-queue-cancel')),
  }));
  expect(headerCheck.appbarInputs).toBe(0);
  expect(headerCheck.modalInsideAppbar).toBe(false);

  await expect.poll(() => cancelCalls.some((c) => c.inboxId === 9023 && c.body.dry_run === true)).toBe(true);
  const tombChk = page.locator('#queue-cancel-tombstone');
  await expect(tombChk).not.toBeChecked();
  await page.locator('#queue-cancel-confirm').click();
  await expect.poll(() => cancelCalls.some((c) => c.inboxId === 9023 && c.body.dry_run === false && c.body.tombstone === false)).toBe(true);
  await expect(modal).toBeHidden();
  await expect(pop).toBeVisible();
  await expect(pop.locator('.qa-row.st-queued')).toHaveCount(1);

  // 2. Staged item with claimed_active=true shows warning badge and locks confirm until #queue-cancel-force is checked.
  const stagedRow = pop.locator('.qa-row.st-staged').first();
  await expect(stagedRow.locator('.q-chip.is-cancelling')).toContainText(/에이전트 추출 중|Agent extracting/);
  await stagedRow.locator('[data-queue-action="cancel-staged"]').click();
  await expect(modal).toBeVisible();
  await expect.poll(() => cancelCalls.some((c) => c.inboxId === 9025 && c.body.dry_run === true)).toBe(true);
  const confirmBtn = page.locator('#queue-cancel-confirm');
  const forceChk = page.locator('#queue-cancel-force');
  await expect(page.locator('#queue-cancel-warn')).toBeVisible();
  await expect(page.locator('#queue-cancel-warn')).toContainText('antigravity-agent');
  await expect(confirmBtn).toBeDisabled();
  await forceChk.check();
  await expect(confirmBtn).toBeEnabled();
  await confirmBtn.click();
  await expect.poll(() => cancelCalls.some((c) => c.inboxId === 9025 && c.body.dry_run === false && c.body.force === true)).toBe(true);
  await expect(modal).toBeHidden();
  await expect(pop).toBeVisible();
  await expect(pop.locator('.qa-row.st-staged')).toHaveCount(0);

  // 3. Done item purge shows Dry-Run impact counts and tombstone checked by default.
  const doneRow = pop.locator('.qa-row.st-done').first();
  await doneRow.locator('[data-queue-action="purge-done"]').click();
  await expect(modal).toBeVisible();
  await expect.poll(() => cancelCalls.some((c) => c.inboxId === 500 && c.body.dry_run === true)).toBe(true);
  const impactCells = page.locator('#queue-cancel-impact .queue-impact-cell strong');
  await expect(impactCells).toHaveCount(5);
  await expect(impactCells.nth(0)).toHaveText('1');
  await expect(impactCells.nth(1)).toHaveText('3');
  await expect(impactCells.nth(2)).toHaveText('2');
  await expect(impactCells.nth(3)).toHaveText('4');
  await expect(impactCells.nth(4)).toHaveText('2');
  await expect(page.locator('#queue-cancel-warn')).toBeVisible();
  await expect(tombChk).toBeChecked();
  await confirmBtn.click();
  await expect.poll(() => cancelCalls.some((c) => c.inboxId === 500 && c.body.dry_run === false && c.body.tombstone === true)).toBe(true);
  await expect(modal).toBeHidden();
  await expect(pop).toBeVisible();
  await expect(pop.locator('.qa-row.st-done')).toHaveCount(0);

  // 4. Running item cancel posts to /jobs/{job_id}/cancel, shows cancelling badge, and hides cancel button.
  const firstRunningRow = pop.locator('.qa-row.st-running').first();
  await firstRunningRow.locator('[data-queue-action="cancel-running"]').click();
  await expect(modal).toBeVisible();
  await expect(tombChk).not.toBeChecked();
  await confirmBtn.click();
  await expect.poll(() => jobCancelCalls.some((c) => c.jobId === 'job_9021' && c.body.tombstone === false)).toBe(true);
  await expect(modal).toBeHidden();
  await expect(pop).toBeVisible();
  await expect(firstRunningRow.locator('.q-chip.is-cancelling')).toContainText(/중단 예약됨|Cancel requested/);
  await expect(firstRunningRow.locator('[data-queue-action="cancel-running"]')).toHaveCount(0);

  // 5. Switch to Option C: collapsed rows have zero action buttons and show cancelling badge; expanded rows expose actions inside .qc-detail-actions.
  await page.locator('#queue-view-toggle').click();
  await expect(pop).toHaveAttribute('data-queue-view', 'compact');
  await expect(pop.locator('.qc-row .qc-row-btn [data-queue-action]')).toHaveCount(0);
  await expect(pop.locator('.qc-row .qc-title-row .q-chip.is-cancelling').first()).toContainText(/중단 예약됨|Cancel requested/);

  const secondRunningBtn = pop.locator('.qc-row.st-running .qc-row-btn').nth(1);
  await secondRunningBtn.click();
  await expect(secondRunningBtn).toHaveAttribute('aria-expanded', 'true');
  const secondRunningDetailActions = pop.locator('.qc-row.st-running').nth(1).locator('.qc-detail-actions');
  await expect(secondRunningDetailActions.locator('[data-queue-action="cancel-running"]')).toBeVisible();
  await secondRunningDetailActions.locator('[data-queue-action="cancel-running"]').click();
  await expect(modal).toBeVisible();
  await confirmBtn.click();
  await expect.poll(() => jobCancelCalls.some((c) => c.jobId === 'job_9022')).toBe(true);
  await expect(modal).toBeHidden();
  await expect(pop.locator('.qc-row.st-running').nth(1).locator('.q-chip.is-cancelling')).toContainText(/중단 예약됨|Cancel requested/);

  const headerCheckAfter = await page.evaluate(() => ({
    appbarInputs: document.querySelectorAll('header.appbar input').length,
    modalInsideAppbar: Boolean(document.querySelector('header.appbar #modal-queue-cancel')),
  }));
  expect(headerCheckAfter.appbarInputs).toBe(0);
  expect(headerCheckAfter.modalInsideAppbar).toBe(false);

  await context.close();
  expect(errors).toEqual([]);
});
