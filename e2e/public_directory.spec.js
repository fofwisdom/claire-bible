// The public account directory at /directory (KNOWLEDGE_FEDERATION_DESIGN.md Section 8.3 rule 2). It is in both server modes; the server of this test directory is in single mode.
// In single mode the account page of the admin has the USERNAME and the switch of the public profile, as in network mode (Section 15 rules 4 and 5). The old path /explore/accounts is gone.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('single mode: the admin makes the profile public on the account page, and /directory lists the admin', async ({ page, request }) => {
  const errors = watchErrors(page);

  // Before the change: the admin has a private profile, so the directory is empty. The actor profile URL still serves 200 with rel="alternate", and the old explore paths give 404.
  expect((await request.get('/explore/accounts')).status()).toBe(404);
  expect((await request.get('/explore/federation')).status()).toBe(404);
  const profileRes = await request.get('/@e2eadmin/');
  expect(profileRes.status()).toBe(200);
  expect(await profileRes.text()).toContain('rel="alternate" type="application/activity+json"');
  const before = await request.get('/directory', { headers: { Accept: 'application/json' } });
  expect(before.status()).toBe(200);
  expect((await before.json()).totalItems).toBe(0);

  await authenticateOwner(page, 'pubdirect1');
  await page.goto('/account/profile', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-account-field="username"]')).toHaveText('@e2eadmin');
  const toggle = page.locator('input[data-settings-toggle="profile-public"]');
  await expect(toggle).not.toBeChecked();
  const saved = page.waitForResponse((r) => r.url().endsWith('/api/me') && r.request().method() === 'PATCH');
  await toggle.check();
  expect((await saved).status()).toBe(200);
  await expect(toggle).toBeEnabled();

  // The directory page of a browser: one card with the handle and a link to the namespace of the admin.
  await page.goto('/directory', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('.account-card')).toHaveCount(1);
  await expect(page.locator('.account-card .account-handle')).toHaveText('@e2eadmin');
  await expect(page.locator('.account-card a.account-name')).toHaveAttribute('href', '/@e2eadmin/');

  // The public appbar links to /directory, and the link opens the directory in single mode too.
  await page.goto('/themes', { waitUntil: 'domcontentloaded' });
  const link = page.locator('header.appbar .pub-appbar-nav a[href="/directory"]');
  await expect(link).toHaveCount(1);
  await link.click();
  await expect(page).toHaveURL(/\/directory$/);
  await expect(page.locator('.account-card .account-handle')).toHaveText('@e2eadmin');

  // Restore the private profile for the next tests of this server.
  await page.goto('/account/profile', { waitUntil: 'domcontentloaded' });
  const restored = page.waitForResponse((r) => r.url().endsWith('/api/me') && r.request().method() === 'PATCH');
  await page.locator('input[data-settings-toggle="profile-public"]').uncheck();
  expect((await restored).status()).toBe(200);
  expect(errors).toEqual([]);
});
