// The page /support/pipeline (api/accounts_routes/pipeline.py) against the real E2E server: Tabs + sticky ToC, Proposal 4 Gemini Neon shared component, Beat 04 5-Gate meter, admin Jev pill, cross-tab ToC navigation, and hash link from /admin/providers.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, expectNoRawKeys, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('pipeline page: Gemini Neon 5-stage spine, mode toggle, 5-gate meter, admin Jev pill, cross-tab ToC, and zero overflow', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1360, height: 900 });
  await authenticateOwner(page, 'pipeline01');
  await page.goto('/support/pipeline', { waitUntil: 'domcontentloaded' });

  await expect(page.locator('[data-nav-section="pipeline"][aria-current="page"]')).toBeVisible();
  await expect(page.locator('#pipeline-tabs [role="tab"]')).toHaveCount(3);
  await expect(page.locator('#pipeline-tabs [role="tab"][data-tab="flow"]')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#console-layout.has-toc')).toBeVisible();
  await expect(page.locator('#console-toc')).toBeVisible();
  await expect(page.locator('#console-toc-list [data-toc-group]')).toHaveCount(3);

  // Proposal 4 Gemini Neon mount inside .console-subcard#pipeline-graph
  const graphCard = page.locator('#pipeline-graph');
  await expect(graphCard).toBeVisible();
  await expect(graphCard.locator('.p4-theatre')).toBeVisible();
  await expect(graphCard.locator('.p4-node')).toHaveCount(5);
  await expect(graphCard.locator('[data-jev-legend]')).toBeVisible();

  // Motion button has no aria-pressed
  const motionBtn = graphCard.locator('#p4-motion-btn');
  await expect(motionBtn).toBeVisible();
  await expect(motionBtn).not.toHaveAttribute('aria-pressed', /.*/);

  // Switch to Beat 04 (index 3) and test 5-Gate meter + admin Jev pill
  await graphCard.locator('.p4-node[data-p4-stage="3"]').click();
  await expect(graphCard.locator('.p4-node[data-p4-stage="3"]')).toHaveAttribute('aria-current', 'step');
  await expect(graphCard.locator('.p4-gate-row')).toHaveCount(5);
  await expect(graphCard.locator('[data-jev-pill]')).toBeVisible();
  await graphCard.locator('[data-p4-gate-preset="auto"]').click();
  await expect(graphCard.locator('[data-p4-gate-result]')).toContainText('Gate 3');
  await graphCard.locator('[data-p4-gate-preset="border"]').click();
  await expect(graphCard.locator('[data-p4-gate-result]')).toContainText('Gate 4');

  // Toggle 서버 API <-> 에이전트 클라이언트 (borderline 0.84 skips Gate 4 and routes to Gate 5 in client mode)
  await graphCard.locator('[data-p4-mode="client"]').click();
  await expect(graphCard.locator('[data-p4-mode="client"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(graphCard.locator('[data-jev-pill]')).toHaveCount(0);
  await expect(graphCard.locator('[data-p4-gate-result]')).toContainText('Gate 5');
  await graphCard.locator('[data-p4-mode="provider"]').click();
  await expect(graphCard.locator('[data-jev-pill]')).toBeVisible();
  await expectNoRawKeys(page);

  const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(desktopOverflow).toBeLessThanOrEqual(1);

  // Cross-tab ToC click switches to the Media tab and reveals #pipeline-stt-order, #pipeline-vlm, #pipeline-fixed.
  await page.locator('#console-toc-list a[href="#pipeline-stt-order"]').click();
  await expect(page.locator('#pipeline-tabs [role="tab"][data-tab="media"]')).toHaveAttribute('aria-selected', 'true');
  const order = page.locator('#pipeline-stt-order [data-stt-order]');
  expect(await order.evaluateAll(els => els.map(el => el.getAttribute('data-stt-order')))).toEqual(['manual', 'automatic', 'stt', 'none']);
  await expect(page.locator('#pipeline-stt-order [data-stt-order="manual"]')).toContainText('en');
  await page.locator('#console-toc-list a[href="#pipeline-vlm"]').click();
  await expect(page.locator('#pipeline-vlm')).toBeVisible();
  await page.locator('#console-toc-list a[href="#pipeline-fixed"]').click();
  await expect(page.locator('#pipeline-fixed')).toContainText('RETRIEVAL_DOCUMENT');

  // Cross-tab ToC click switches to the Records tab (#pipeline-recent and #pipeline-fallbacks).
  await page.locator('#console-toc-list a[href="#pipeline-recent"]').click();
  await expect(page.locator('#pipeline-tabs [role="tab"][data-tab="records"]')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#pipeline-recent')).toBeVisible();
  await page.locator('#console-toc-list a[href="#pipeline-fallbacks"]').click();
  await expect(page.locator('#pipeline-fallbacks summary')).toBeVisible();
  await expectNoRawKeys(page);

  // Switch back to Flow tab and verify narrow 390x844 viewport has zero horizontal overflow.
  await page.locator('#pipeline-tabs [role="tab"][data-tab="flow"]').click();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(graphCard.locator('.p4-theatre')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
});

test('admin providers: the STT card links to /support/pipeline#pipeline-stt-order and opens the media tab', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1360, height: 900 });
  await authenticateOwner(page, 'pipeline02');
  await page.goto('/admin/providers#prov-stt-form', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#prov-tabs [role="tab"][data-tab="stt"]')).toHaveAttribute('aria-selected', 'true');

  const sttOrder = page.locator('#prov-stt-form #prov-stt-order');
  await expect(sttOrder).toBeVisible();
  expect(await sttOrder.locator('[data-stt-order]').evaluateAll(els => els.map(el => el.getAttribute('data-stt-order')))).toEqual(['manual', 'automatic', 'stt']);
  await expect(sttOrder).toContainText('서버 기본 언어');
  await expect(page.locator('#prov-effective-card a[data-prov-pipeline-link]')).toHaveAttribute('href', '/support/pipeline');
  await expect(page.locator('#prov-effective-card')).toBeHidden();
  await expectNoRawKeys(page);

  await sttOrder.locator('a[href="/support/pipeline#pipeline-stt-order"]').click();
  await expect(page).toHaveURL(/\/support\/pipeline#pipeline-stt-order$/);
  await expect(page.locator('#pipeline-tabs [role="tab"][data-tab="media"]')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#pipeline-stt-order')).toBeVisible();
  await expect(page.locator('#pipeline-stt-order')).toBeInViewport();
  expect(errors).toEqual([]);
});
