const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /settings/bundles against the real E2E server: each row of the support bundle list and of the data bundle list has a copy link control. The list API gives the token address to the owner, thus the control stays after a reload of the page.

const COPY_LABEL = /^(Copy link|링크 복사)$/;

async function copyFromRow(page, section) {
  const copy = section.locator('tbody tr').first().getByRole('button', { name: COPY_LABEL });
  await expect(copy).toBeVisible();
  await copy.click();
  await expect(section.locator('[data-feature-result]')).toHaveAttribute('data-state', 'ok');
  return page.evaluate(() => navigator.clipboard.readText());
}

test('each bundle row keeps its copy link after a reload, and the link opens without a session', async ({ page, context, baseURL, playwright }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: new URL(baseURL).origin });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'bundlelinks');
  await page.goto('/settings/bundles', { waitUntil: 'domcontentloaded' });

  const support = page.locator("section.card:has([data-feature-list='support-bundles'])");
  const data = page.locator("section.card:has([data-feature-list='data-bundles'])");
  await support.locator("form[data-feature-form='support-bundle-create'] button[type='submit']").click();
  await expect(support.locator('tbody tr')).not.toHaveCount(0);
  const hasDataForm = await data.locator("form[data-feature-form='data-bundle-create']").count();
  if (hasDataForm) {
    await data.locator("form[data-feature-form='data-bundle-create'] button[type='submit']").click();
    await expect(data.locator('tbody tr')).not.toHaveCount(0, { timeout: 30000 });
  }

  // A reload loses each value that the create response gave. The copy link comes from the list.
  await page.reload({ waitUntil: 'domcontentloaded' });
  const links = [[await copyFromRow(page, support), /\/support\/bundle\?token=sb3_/]];
  if (hasDataForm) links.push([await copyFromRow(page, data), /\/graph\/bundle\?token=gb3_/]);

  const anonymous = await playwright.request.newContext({ baseURL });
  try {
    for (const [link, shape] of links) {
      expect(link).toMatch(shape);
      const url = new URL(link);
      const res = await anonymous.get(url.pathname + url.search);
      expect(res.status(), link).toBe(200);
      expect(res.headers()['cache-control']).toContain('no-store');
    }
  } finally {
    await anonymous.dispose();
  }

  await expectNoRawKeys(page, "[data-feature-section='bundles']");
  // Delete the bundles of this test: an account can have 5 support bundles at the same time.
  for (const section of hasDataForm ? [support, data] : [support]) {
    const rows = section.locator('tbody tr');
    while (await rows.count()) {
      const before = await rows.count();
      await rows.first().locator('button[title]').last().click();
      await expect(rows).not.toHaveCount(before);
    }
  }
  expect(errors).toEqual([]);
});
