const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys, installJsonApi } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /admin/limits against answers in the shapes of api/server.py: "limits_get_route" (IngestLimitManager.get_sanitized_config) and "limits_update_route" ({"ok", "config"}, or 400 with "detail" from IngestLimitManager.update_config). The answers are given here, so the test does not change data/ingest_limits.json of the repository.

const DEFAULT_LIMITS = { raw_char_budget: 20000, pdf_max_extract_chars: 50000, pdf_paper_threshold_chars: 15000, pdf_exclude_appendix: true, pdf_exclude_references: true, pdf_parser: 'default', pdf_detect_encoding_flaws: true, pdf_paper_effort: 'high', pdf_default_effort: '', pdf_classifier_effort: 'low', extract_char_budget: 20000, merged_extract_char_budget: 0, slicing_strategy: 'table-exemption', embed_char_budget: 8000, expand_char_budget: 2000, expand_max: 5, auto_expand: true, same_subject_merge: 'off', watch_interval_days: 1.0, research_context_budget: 8000, presentation_pdf_max_bytes: 67108864, video_max_extract_chars: 200000, video_chunk_duration_sec: 240, vlm_max_images: 20 };

test('/admin/limits: the server administrator sets automatic expansion, the shadow mode of the same-subject source merge, and the recheck interval of watch', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'admlimits1');
  const limits = { ...DEFAULT_LIMITS };
  const { calls, unknown } = await installJsonApi(page, (u) => u.pathname === '/limits', ({ method, body }) => {
    if (method === 'GET') return [{ version: 1, limits, updated_at: 1700000000 }];
    if (method === 'PATCH') {
      const days = Number(body.limits.watch_interval_days);
      if (!(days > 0)) return [{ detail: 'watch_interval_days must be greater than 0' }, 400];
      Object.assign(limits, body.limits, { watch_interval_days: days });
      return [{ ok: true, config: { version: 1, limits, updated_at: 1700000100 } }];
    }
    return null;
  });

  await page.goto('/admin/limits', { waitUntil: 'domcontentloaded' });
  const section = page.locator('#limit-expand-section');
  await expect(section).toBeVisible();
  const autoExpand = page.locator('#limit-auto-expand');
  const watchDays = page.locator('#limit-watch-interval-days');
  const expandMax = page.locator('#limit-expand-max');
  // The three controls of the expansion are in the same section, with the values from the server.
  await expect(autoExpand).toBeChecked();
  await expect(watchDays).toHaveValue('1');
  await expect(expandMax).toHaveValue('5');
  await expect(section.locator('#limit-expand-max')).toHaveCount(1);
  // The same-subject source merge has two modes only: off (the default) and shadow. No mode merges.
  const mergeMode = section.locator('#limit-same-subject-merge');
  await expect(mergeMode).toHaveValue('off');
  await expect(mergeMode.locator('option')).toHaveCount(2);
  expect(await mergeMode.locator('option').evaluateAll((els) => els.map((e) => e.value))).toEqual(['off', 'shadow']);
  await expectNoRawKeys(page);

  // A value of 0 days is refused by the server, and the panel shows the reason.
  await watchDays.fill('0');
  await page.locator('#admin-limits-host button', { hasText: /Save|저장/ }).first().click();
  await expect(page.locator('#admin-limits-host')).toContainText('watch_interval_days must be greater than 0');

  // A valid change goes to the server, and the panel shows the saved values.
  await page.locator('#limit-auto-expand').uncheck();
  await page.locator('#limit-watch-interval-days').fill('2.5');
  await page.locator('#limit-expand-max').fill('3');
  await page.locator('#limit-same-subject-merge').selectOption('shadow');
  await page.locator('#admin-limits-host button', { hasText: /Save|저장/ }).first().click();
  await expect(page.locator('#limit-auto-expand')).not.toBeChecked();
  await expect(page.locator('#limit-watch-interval-days')).toHaveValue('2.5');
  await expect(page.locator('#limit-expand-max')).toHaveValue('3');

  const patches = calls.filter((c) => c.method === 'PATCH');
  expect(patches).toHaveLength(2);
  expect(patches[1].body.limits).toMatchObject({ auto_expand: false, same_subject_merge: 'shadow', watch_interval_days: '2.5', expand_max: 3 });

  // After a reload, the panel shows the stored values.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('#limit-auto-expand')).not.toBeChecked();
  await expect(page.locator('#limit-watch-interval-days')).toHaveValue('2.5');
  await expect(page.locator('#limit-same-subject-merge')).toHaveValue('shadow');

  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

// The review of the shadow records of the same-subject source merge, against answers in the shapes of api/server.py: "admin_expansion_shadow_route" ({mode, report, records}) and "admin_expansion_shadow_verdict_route" ({ok, record, report}). The verdict is given only here; the CLI of the agents reads only.
function shadowReport(records) {
  const judged = records.filter((r) => r.verdict);
  const count = (model, human) => judged.filter((r) => r.same_subject === model && (r.verdict === 'same') === human).length;
  const tp = count(true, true), fp = count(true, false), fn = count(false, true), tn = count(false, false);
  const ratio = (a, b) => (b ? a / b : null);
  return { records: records.length, judged: judged.length, pending: records.length - judged.length, tp, fp, fn, tn, precision: ratio(tp, tp + fp), recall: ratio(tp, tp + fn), accuracy: ratio(tp + tn, judged.length) };
}

test('/admin/limits: the server administrator gives verdicts to the shadow records, and the report follows', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'admlimits2');
  const records = [
    { id: 11, parent: { document_id: 'doc_p', title: 'GeekNews: Tool X', url: 'https://news.example/x' }, child: { document_id: 'doc_c1', title: 'Tool X repository', url: 'https://git.example/x' }, same_subject: true, relevance: 0.92, quality: 0.81, interpretation: '', reason: 'the official repository of the tool', created_at: 1700000200, verdict: null, verdict_note: '', verdict_at: null },
    { id: 10, parent: { document_id: 'doc_p', title: 'GeekNews: Tool X', url: 'https://news.example/x' }, child: { document_id: 'doc_c2', title: 'Tool Y', url: 'https://y.example/' }, same_subject: false, relevance: 0.75, quality: 0.7, interpretation: '', reason: 'a different project', created_at: 1700000100, verdict: null, verdict_note: '', verdict_at: null },
  ];
  const { calls, unknown } = await installJsonApi(page, (u) => u.pathname === '/limits' || u.pathname.startsWith('/api/admin/expansion/'), ({ path, method, query, body }) => {
    if (path === '/limits' && method === 'GET') return [{ version: 1, limits: { ...DEFAULT_LIMITS, same_subject_merge: 'shadow' }, updated_at: 1700000000 }];
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

  await page.goto('/admin/limits', { waitUntil: 'domcontentloaded' });
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
