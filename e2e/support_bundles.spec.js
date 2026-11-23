const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /support/bundles against the real E2E server: a support bundle made from the Silvercord link of a document, a data bundle, and the copy link of each. The page gives links only (no download). The list API gives the link to the owner, thus the control stays after a reload of the page.

const COPY_LABEL = /^(Copy link|링크 복사)$/;
// The seed permalink of doc-1 (e2e/seed_db.py), a document of the administrator.
const DOC_TOKEN = '23456789abcdefgh';

async function copyFrom(page, scope, form) {
  const copy = scope.getByRole('button', { name: COPY_LABEL }).first();
  await expect(copy).toBeVisible();
  await copy.click();
  await expect(form.locator('[data-feature-result]')).toHaveAttribute('data-state', 'ok');
  return page.evaluate(() => navigator.clipboard.readText());
}

test('a support bundle from a document link and a data bundle each give a link to copy, and the link opens without a session', async ({ page, context, baseURL, playwright }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: new URL(baseURL).origin });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'bundlelinks');
  await page.goto('/support/bundles', { waitUntil: 'domcontentloaded' });
  await expect(page.locator("body[data-active-area='support'][data-active-section='bundles']")).toHaveCount(1);
  await expect(page.locator(".console-nav-link[data-nav-section='bundles'][aria-current='page']")).toHaveAttribute('href', '/support/bundles');

  const support = page.locator('#support-bundles-card');
  const data = page.locator('#data-bundles-card');
  const supportForm = support.locator("form[data-feature-form='support-bundle-create']");

  // A link that names no note or document of the account is refused with a message.
  await supportForm.locator("input[name='target']").fill('not a link of this account');
  await supportForm.locator("button[type='submit']").click();
  await expect(supportForm.locator('[data-feature-result]')).toHaveAttribute('data-state', 'error');
  await expect(supportForm.locator('[data-bundle-link]')).toHaveCount(0);

  // The link of a document: the new bundle shows its link at once, with a copy control.
  const docLink = new URL('/silvercord/' + DOC_TOKEN, baseURL).href;
  await supportForm.locator("input[name='target']").fill(docLink);
  await supportForm.locator("select[name='scope']").selectOption('account');
  await supportForm.locator("button[type='submit']").click();
  const created = supportForm.locator('[data-bundle-link]');
  await expect(created).toHaveValue(/\/support\/bundle\/sb3_/);
  const createdLink = await created.inputValue();
  expect(await copyFrom(page, supportForm.locator("[data-bundle-created='support']"), supportForm)).toBe(createdLink);
  await expect(support.locator('tbody tr').first()).toContainText('/silvercord/' + DOC_TOKEN);

  const hasDataForm = await data.locator("form[data-feature-form='data-bundle-create']").count();
  if (hasDataForm) {
    const dataForm = data.locator("form[data-feature-form='data-bundle-create']");
    await dataForm.locator("button[type='submit']").click();
    await expect(dataForm.locator('[data-bundle-link]')).toHaveValue(/\/support\/graph-bundle\/gb3_/, { timeout: 30000 });
  }

  // No download control on the page: each bundle gives its link only.
  await expect(page.locator("[data-feature-section='bundles'] a[download]")).toHaveCount(0);

  // A reload loses each value that the create response gave. The copy link of a row comes from the list.
  await page.reload({ waitUntil: 'domcontentloaded' });
  const links = [[await copyFrom(page, support.locator('tbody tr').first(), supportForm), /\/support\/bundle\/sb3_/]];
  expect(links[0][0]).toBe(createdLink);
  if (hasDataForm) links.push([await copyFrom(page, data.locator('tbody tr').first(), data.locator("form[data-feature-form='data-bundle-create']")), /\/support\/graph-bundle\/gb3_/]);

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
