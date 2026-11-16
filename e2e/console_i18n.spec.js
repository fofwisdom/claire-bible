const { test, expect } = require('@playwright/test');

// The text of the console (/settings/*, /admin/*) comes from the catalogs (static/locales/{en,ko}.json): the server renders the section text, and the console scripts read the keys console.js.*. The label of the default theme is data of the account, thus the test reads only the controls.
test.use({ locale: 'en-US' });

test('the console shows English text for an English browser, in the server text and in the script text', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  // A login token of the seed database is for one login. This test has its own token (e2e/seed_db.py).
  await page.goto('/?t=e2e-consolei18n-token-0123456789a', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

  // Server text of a section, and the theme panel that manage_panels.js renders.
  await page.goto('/admin/themes', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page.locator('#admin-themes-summary h2')).toContainText('Theme slots of the full instance');
  const themes = page.locator('#admin-themes-host');
  await expect(themes.locator('h2')).toHaveText(/Knowledge theme management/);
  await expect(themes.getByRole('button', { name: 'Show options' }).first()).toBeVisible();
  await expect(themes.getByText('Add a new theme')).toBeVisible();

  // The provider panel (administrator area): script text with parameters.
  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });
  const providers = page.locator('#admin-providers-host');
  await expect(providers.locator('h2')).toHaveText(/LLM and provider management/);
  await expect(providers.getByRole('button', { name: 'Save the settings' })).toBeVisible();

  // The inline catalog of a console page has the script keys only; a page that is not in the administrator area has no keys of the administrator scripts.
  await page.goto('/settings/account', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#settings-account-info h2')).toContainText('Account information');
  const keys = await page.evaluate(() => Object.keys(JSON.parse(document.getElementById('claire-i18n-catalog').textContent)));
  expect(keys.some((key) => key.startsWith('console.js.account.'))).toBe(true);
  expect(keys.filter((key) => key.startsWith('console.') && !key.startsWith('console.js.'))).toEqual([]);
  expect(keys.filter((key) => key.startsWith('console.js.admin.'))).toEqual([]);

  // The language selector changes the console to Korean.
  await page.locator('#pref-account-locale').selectOption('ko');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
  await expect(page.locator('#settings-account-info h2')).toContainText('계정 정보');
  await page.goto('/admin/themes', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#admin-themes-host h2')).toHaveText(/지식 테마 관리/);

  // The stored language is a setting of the seed account, which other tests share: set it back to automatic.
  await page.goto('/settings/account', { waitUntil: 'domcontentloaded' });
  await page.locator('#pref-account-locale').selectOption('');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
});
