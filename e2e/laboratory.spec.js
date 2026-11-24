// The laboratory (LABORATORY_DESIGN.md) against the real E2E server: /laboratory/measurements (measurement M0) and /laboratory/shadows (Jev, the same-subject source merge and its review, the shadow embedding spaces). The saves and the runs are answered here, so the test does not change providers.json, ingest_limits.json, or the run records of the E2E server.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys, installJsonApi } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('/laboratory/measurements: the form tells the cost, starts a run, and follows it until it ends', async ({ page }) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'laboratory1');
  await page.goto('/laboratory', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/laboratory\/measurements$/);
  await expect(page.locator('[data-nav-group="laboratory"] [aria-current="page"]')).toHaveAttribute('href', '/laboratory/measurements');
  const card = page.locator('#lab-stt-hints');
  await expect(card.locator('[data-lab-cost]')).toContainText('300');
  await expect(card.locator('input[name="hints"]')).not.toHaveValue('');
  await expectNoRawKeys(page);

  // The E2E server has no key: the button is off and the page tells why. The test turns the button on and answers the API.
  if (await page.locator('[data-lab-no-key]').count()) await card.locator('button[type="submit"]').evaluate((b) => { b.disabled = false; });
  let polls = 0;
  const { calls } = await installJsonApi(page, (u) => u.pathname.startsWith('/api/admin/laboratory/'), ({ path, method }) => {
    if (path === '/api/admin/laboratory/stt-hints' && method === 'POST') return [{ run: { id: 'run1', status: 'running' } }, 202];
    if (path === '/api/admin/laboratory/runs/run1' && method === 'GET') { polls += 1; return [{ run: { id: 'run1', status: polls > 1 ? 'done' : 'running' } }]; }
    return null;
  });
  await card.locator('input[name="url"]').fill('https://www.youtube.com/watch?v=example');
  await card.locator('input[name="hints"]').fill('ko, en');
  await card.locator('button[type="submit"]').click();
  await expect(card.locator('[data-lab-result]')).toHaveAttribute('data-state', 'busy');
  await page.waitForEvent('load', { timeout: 20_000 });
  expect(calls.find((c) => c.method === 'POST').body).toEqual({ url: 'https://www.youtube.com/watch?v=example', hints: ['ko', 'en'] });
  expect(polls).toBeGreaterThanOrEqual(2);
  expect(errors).toEqual([]);
});

test('/laboratory/shadows: Jev and the same-subject mode save next to their buttons; the old pages link here', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'laboratory2');
  const { calls } = await installJsonApi(page, (u) => u.pathname === '/providers' || u.pathname === '/limits', ({ path, method, body }) => {
    if (method !== 'PATCH') return 'fallback';
    return [{ ok: true, config: body }];
  });
  await page.goto('/laboratory/shadows', { waitUntil: 'domcontentloaded' });
  for (const id of ['#lab-jev', '#lab-merge', '#admin-expansion-review-host', '#lab-embedding']) await expect(page.locator(id)).toBeVisible();
  await expectNoRawKeys(page);

  const jev = page.locator('#lab-jev form');
  const jevOn = await page.locator('#lab-jev-enabled').isChecked();
  await page.locator('#lab-jev-enabled').setChecked(!jevOn);
  await jev.locator('button[type="submit"]').click();
  await expect(jev.locator('[data-lab-result]')).toHaveAttribute('data-state', 'ok');
  await expect(page.locator('#lab-jev [data-lab-jev-state]')).toHaveAttribute('data-lab-jev-state', jevOn ? 'off' : 'on');

  const merge = page.locator('#lab-merge form');
  await merge.locator('[data-lab-merge-mode]').selectOption('shadow');
  await merge.locator('button[type="submit"]').click();
  await expect(merge.locator('[data-lab-result]')).toHaveAttribute('data-state', 'ok');
  await expect(page.locator('#lab-merge [data-lab-merge-state]')).toHaveAttribute('data-lab-merge-state', 'shadow');
  expect(calls.map((c) => [c.path, c.body])).toEqual([
    ['/providers', { providers: { jev: { enabled: !jevOn, base_url: await page.locator('#lab-jev-url').inputValue() } } }],
    ['/limits', { same_subject_merge: 'shadow' }],
  ]);

  // The old places link here.
  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });
  await page.locator('#prov-tabs [role="tab"][data-tab="advanced"]').click();
  await expect(page.locator('[data-prov-jev-moved] a')).toHaveAttribute('href', '/laboratory/shadows#lab-jev');
  await expect(page.locator('#prov-jev-enabled')).toHaveCount(0);
  expect(errors).toEqual([]);
});

function shadowReport(records) {
  const judged = records.filter((r) => r.verdict);
  const count = (model, human) => judged.filter((r) => r.same_subject === model && (r.verdict === 'same') === human).length;
  const tp = count(true, true), fp = count(true, false), fn = count(false, true), tn = count(false, false);
  const ratio = (a, b) => (b ? a / b : null);
  return { records: records.length, judged: judged.length, pending: records.length - judged.length, tp, fp, fn, tn, precision: ratio(tp, tp + fp), recall: ratio(tp, tp + fn), accuracy: ratio(tp + tn, judged.length) };
}

test('/laboratory/shadows: the server administrator gives verdicts to the shadow records, and the report follows', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'admlimits2');
  const records = [
    { id: 11, parent: { document_id: 'doc_p', title: 'GeekNews: Tool X', url: 'https://news.example/x' }, child: { document_id: 'doc_c1', title: 'Tool X repository', url: 'https://git.example/x' }, same_subject: true, relevance: 0.92, quality: 0.81, interpretation: '', reason: 'the official repository of the tool', created_at: 1700000200, verdict: null, verdict_note: '', verdict_at: null },
    { id: 10, parent: { document_id: 'doc_p', title: 'GeekNews: Tool X', url: 'https://news.example/x' }, child: { document_id: 'doc_c2', title: 'Tool Y', url: 'https://y.example/' }, same_subject: false, relevance: 0.75, quality: 0.7, interpretation: '', reason: 'a different project', created_at: 1700000100, verdict: null, verdict_note: '', verdict_at: null },
  ];
  const { calls, unknown } = await installJsonApi(page, (u) => u.pathname.startsWith('/api/admin/expansion/'), ({ path, method, query, body }) => {
    if (path === '/api/admin/expansion/shadow' && method === 'GET') {
      const shown = query.get('pending') === '1' ? records.filter((r) => !r.verdict) : records;
      return [{ mode: 'shadow', report: shadowReport(records), records: shown }];
    }
    const m = path.match(/^\/api\/admin\/expansion\/shadow\/(\d+)\/verdict$/);
    if (m && method === 'PUT') {
      const rec = records.find((r) => r.id === Number(m[1]));
      if (!rec) return [{ error: 'shadow_record_not_found' }, 404];
      rec.verdict = body.verdict; rec.verdict_note = body.verdict ? body.note : ''; rec.verdict_at = body.verdict ? 1700000300 : null;
      return [{ ok: true, record: rec, report: shadowReport(records) }];
    }
    return null;
  });

  await page.goto('/laboratory/shadows', { waitUntil: 'domcontentloaded' });
  const review = page.locator('#admin-expansion-review-host');
  await expect(review.locator('[data-record]')).toHaveCount(2);
  await expect(review.locator('[data-review-mode-off]')).toHaveCount(0);
  await expect(review.locator('[data-metric="precision"]')).not.toContainText('%');
  await expect(review.locator('[data-record="11"]')).toContainText('Tool X repository');
  await expect(review.locator('[data-record="11"]')).toContainText('the official repository of the tool');
  await expectNoRawKeys(page);

  // The model is right about #11 and wrong about #10.
  await review.locator('[data-record="11"] [data-review-note]').fill('official repository');
  await review.locator('[data-record="11"] [data-verdict="same"]').click();
  await expect(review.locator('[data-record]')).toHaveCount(1);
  await review.locator('[data-record="10"] [data-verdict="same"]').click();
  await expect(review.locator('[data-review-empty]')).toBeVisible();
  await expect(review.locator('[data-metric="precision"]')).toHaveText('100%');
  await expect(review.locator('[data-metric="recall"]')).toHaveText('50%');

  // All records: the verdict shows, and it can be removed.
  await review.locator('[data-review-pending]').uncheck();
  await expect(review.locator('[data-record]')).toHaveCount(2);
  await expect(review.locator('[data-record="11"] [data-review-verdict]')).toContainText('official repository');
  await review.locator('[data-record="10"] [data-verdict="clear"]').click();
  await expect(review.locator('[data-metric="recall"]')).toHaveText('100%');

  const puts = calls.filter((c) => c.method === 'PUT');
  expect(puts.map((c) => [c.path, c.body])).toEqual([
    ['/api/admin/expansion/shadow/11/verdict', { verdict: 'same', note: 'official repository' }],
    ['/api/admin/expansion/shadow/10/verdict', { verdict: 'same', note: '' }],
    ['/api/admin/expansion/shadow/10/verdict', { verdict: null, note: '' }],
  ]);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});
