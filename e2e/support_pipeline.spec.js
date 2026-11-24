// The page /support/pipeline (api/accounts_routes/pipeline.py) against the real E2E server: Tabs + sticky ToC, SVG diagram without overflow, accurate server API & agent client steps, Jev side note, cross-tab ToC navigation, and hash link from /admin/providers.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, expectNoRawKeys, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('pipeline page: tabs with sticky ToC, SVG graph, accurate flow, Jev note, and zero overflow', async ({ page }) => {
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

  // SVG overview diagram and legend
  const svg = page.locator('#pipeline-lineage-svg');
  await expect(svg).toBeVisible();
  const svgBox = await svg.boundingBox();
  expect(svgBox.width).toBeGreaterThan(300);
  expect(svgBox.height).toBeGreaterThan(150);
  await expect(page.locator('#pipeline-graph .pipeline-graph-legend')).toBeVisible();

  // Server API flow steps, Jev side note, client flow, and other AI card
  const steps = page.locator('#pipeline-api-flow [data-flow-step]');
  await expect(steps).toHaveCount(13);
  expect(await steps.evaluateAll(els => els.map(el => el.getAttribute('data-flow-step')))).toEqual([
    'input',
    'classify',
    'check',
    'effort',
    'extract',
    'summary',
    'detail',
    'resolve',
    'record',
    'relations',
    'links',
    'lineage',
    'post',
  ]);
  const resolve = page.locator('[data-flow-step="resolve"]');
  await expect(resolve.locator('[data-flow-gate]')).toHaveCount(5);
  await expect(resolve.locator('.console-flow-side [data-jev-state]')).toBeVisible();
  const mainBox = await resolve.locator('.console-flow-main').boundingBox();
  const sideBox = await resolve.locator('.console-flow-side').boundingBox();
  expect(sideBox.x).toBeGreaterThan(mainBox.x + mainBox.width - 1);

  await expect(page.locator('#pipeline-client-flow [data-client-step]')).toHaveCount(5);
  await expect(page.locator('#pipeline-other')).toBeVisible();
  await expectNoRawKeys(page);

  const desktopOverflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(desktopOverflow).toBeLessThanOrEqual(1);

  // Cross-tab ToC click switches to the Media tab and reveals #pipeline-stt-order, #pipeline-vlm, #pipeline-fixed.
  await page.locator('#console-toc-list a[href="#pipeline-stt-order"]').click();
  await expect(page.locator('#pipeline-tabs [role="tab"][data-tab="media"]')).toHaveAttribute('aria-selected', 'true');
  const order = page.locator('#pipeline-stt-order [data-stt-order]');
  expect(await order.evaluateAll(els => els.map(el => el.getAttribute('data-stt-order')))).toEqual(['manual', 'automatic', 'stt', 'none']);
  await expect(page.locator('#pipeline-stt-order [data-stt-order="manual"]')).toContainText('en');
  await expect(page.locator('#pipeline-vlm')).toBeVisible();
  await expect(page.locator('#pipeline-fixed')).toContainText('RETRIEVAL_DOCUMENT');

  // Cross-tab ToC click switches to the Records tab.
  await page.locator('#console-toc-list a[href="#pipeline-recent"]').click();
  await expect(page.locator('#pipeline-tabs [role="tab"][data-tab="records"]')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#pipeline-fallbacks summary')).toBeVisible();
  await expectNoRawKeys(page);

  // Switch back to Flow tab and verify narrow viewport: side note stacks below and document has zero overflow.
  await page.locator('#pipeline-tabs [role="tab"][data-tab="flow"]').click();
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  const narrowMain = await resolve.locator('.console-flow-main').boundingBox();
  const narrowSide = await resolve.locator('.console-flow-side').boundingBox();
  expect(narrowSide.y).toBeGreaterThan(narrowMain.y + narrowMain.height - 1);
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
