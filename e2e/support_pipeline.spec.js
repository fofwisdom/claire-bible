// The page /support/pipeline (api/accounts_routes/pipeline.py) against the real E2E server: the steps of one document in order with the AI of each step, the order of captions and transcription of a video, and the link from the STT card of /admin/providers to that order.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, expectNoRawKeys, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('pipeline page: the steps in order, Jev beside the resolution, and the video order', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1360, height: 900 });
  await authenticateOwner(page, 'pipeline01');
  await page.goto('/support/pipeline', { waitUntil: 'domcontentloaded' });

  await expect(page.locator('[data-nav-section="pipeline"][aria-current="page"]')).toBeVisible();
  const steps = page.locator('#pipeline-flow [data-flow-step]');
  await expect(steps).toHaveCount(11);
  expect(await steps.evaluateAll(els => els.map(el => el.getAttribute('data-flow-step')))).toEqual(['input', 'check', 'effort', 'extract', 'summary', 'detail', 'resolve', 'record', 'relations', 'links', 'lineage']);
  // The embedding and the judge belong to the resolution step; Jev is a note beside it, not a step between them.
  const resolve = page.locator('[data-flow-step="resolve"]');
  await expect(resolve.locator('[data-flow-gate]')).toHaveCount(5);
  await expect(resolve.locator('.console-flow-side [data-jev-state]')).toBeVisible();
  const mainBox = await resolve.locator('.console-flow-main').boundingBox();
  const sideBox = await resolve.locator('.console-flow-side').boundingBox();
  expect(sideBox.x).toBeGreaterThan(mainBox.x + mainBox.width - 1);

  // The video order: publisher captions, automatic captions, transcription, nothing.
  const order = page.locator('#pipeline-stt-order [data-stt-order]');
  expect(await order.evaluateAll(els => els.map(el => el.getAttribute('data-stt-order')))).toEqual(['manual', 'automatic', 'stt', 'none']);
  await expect(page.locator('#pipeline-stt-order [data-stt-order="manual"]')).toContainText('en');
  await expect(page.locator('#pipeline-fixed')).toContainText('RETRIEVAL_DOCUMENT');
  await expect(page.locator('#pipeline-fallbacks summary')).toBeVisible();
  await expectNoRawKeys(page);

  // A narrow screen: the side note goes under the step and nothing is wider than the page.
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  const narrowMain = await resolve.locator('.console-flow-main').boundingBox();
  const narrowSide = await resolve.locator('.console-flow-side').boundingBox();
  expect(narrowSide.y).toBeGreaterThan(narrowMain.y + narrowMain.height - 1);
  expect(errors).toEqual([]);
});

test('admin providers: the STT card shows the video order and links to the pipeline page', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1360, height: 900 });
  await authenticateOwner(page, 'pipeline02');
  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });

  const sttOrder = page.locator('#prov-stt-form #prov-stt-order');
  await expect(sttOrder).toBeVisible();
  expect(await sttOrder.locator('[data-stt-order]').evaluateAll(els => els.map(el => el.getAttribute('data-stt-order')))).toEqual(['manual', 'automatic', 'stt']);
  await expect(sttOrder).toContainText('CLAIRE_PREFERRED_LANGUAGES');
  await expect(page.locator('#prov-effective-card a[data-prov-pipeline-link]')).toHaveAttribute('href', '/support/pipeline');
  await expectNoRawKeys(page);

  await sttOrder.locator('a[href="/support/pipeline#pipeline-stt-order"]').click();
  await expect(page).toHaveURL(/\/support\/pipeline#pipeline-stt-order$/);
  await expect(page.locator('#pipeline-stt-order')).toBeInViewport();
  expect(errors).toEqual([]);
});
