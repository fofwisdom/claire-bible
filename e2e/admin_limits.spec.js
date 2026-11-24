const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys, installJsonApi } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /admin/limits against answers in the shapes of api/server.py: "limits_get_route" (IngestLimitManager.get_sanitized_config) and "limits_update_route" ({"ok", "config"}, or 400 with "detail" from IngestLimitManager.update_config). The answers are given here, so the test does not change data/ingest_limits.json of the repository.

const DEFAULT_LIMITS = { raw_char_budget: 20000, pdf_max_extract_chars: 50000, pdf_paper_threshold_chars: 15000, pdf_exclude_appendix: true, pdf_exclude_references: true, pdf_paper_effort: 'high', pdf_default_effort: '', pdf_classifier_effort: 'low', extract_char_budget: 20000, merged_extract_char_budget: 0, slicing_strategy: 'table-exemption', embed_char_budget: 8000, expand_max: 5, auto_expand: true, same_subject_merge: 'off', watch_interval_days: 1.0, presentation_pdf_max_bytes: 67108864, video_max_extract_chars: 200000, video_chunk_duration_sec: 240, vlm_max_images: 20 };

test('/admin/limits: the server administrator sets automatic expansion and the recheck interval of watch; the same-subject source merge is in the laboratory', async ({ page }) => {
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
  // The same-subject source merge moved to /laboratory/shadows (LABORATORY_DESIGN.md LB3): a link, no control.
  await expect(section.locator('#limit-same-subject-merge')).toHaveCount(0);
  await expect(section.locator('[data-limit-merge-moved] a')).toHaveAttribute('href', '/laboratory/shadows#lab-merge');
  await expectNoRawKeys(page);

  // A value of 0 days is refused by the server, and the panel shows the reason.
  await watchDays.fill('0');
  await page.locator('#admin-limits-host button', { hasText: /Save|저장/ }).first().click();
  await expect(page.locator('#admin-limits-host')).toContainText('watch_interval_days must be greater than 0');

  // A valid change goes to the server, and the panel shows the saved values.
  await page.locator('#limit-auto-expand').uncheck();
  await page.locator('#limit-watch-interval-days').fill('2.5');
  await page.locator('#limit-expand-max').fill('3');
  await page.locator('#admin-limits-host button', { hasText: /Save|저장/ }).first().click();
  await expect(page.locator('#limit-auto-expand')).not.toBeChecked();
  await expect(page.locator('#limit-watch-interval-days')).toHaveValue('2.5');
  await expect(page.locator('#limit-expand-max')).toHaveValue('3');

  const patches = calls.filter((c) => c.method === 'PATCH');
  expect(patches).toHaveLength(2);
  expect(patches[1].body.limits).toMatchObject({ auto_expand: false, watch_interval_days: '2.5', expand_max: 3 });
  // A save of the limits does not change the mode of the laboratory.
  expect('same_subject_merge' in patches[1].body.limits).toBe(false);

  // After a reload, the panel shows the stored values.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('#limit-auto-expand')).not.toBeChecked();
  await expect(page.locator('#limit-watch-interval-days')).toHaveValue('2.5');

  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});
