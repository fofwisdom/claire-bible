const { test, expect } = require('@playwright/test');

// The text of the console (/settings/*, /moderation/*, /admin/*) comes from the catalogs (static/locales/{en,ko}.json): the server renders the section text, and the console scripts read the keys console.js.*. The label of the default theme is data of the account, thus the test reads only the controls.
test.use({ locale: 'en-US' });

test('the console shows English text for an English browser, in the server text and in the script text', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  // A login token of the seed database is for one login. This test has its own token (e2e/seed_db.py).
  await page.goto('/?t=e2e-consolei18n-token-0123456789a', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

  // Server text of a section, and the 3-tab judgement console that console_admin.js renders.
  await page.goto('/moderation/themes', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  // The old summary of the theme slots is gone: /moderation/themes is the console of the theme judgement (SHARED_KNOWLEDGE_BASES_AND_THEMES_DESIGN.md Section 10.3 rule 4).
  await expect(page.locator('#admin-themes-summary')).toHaveCount(0);
  const themes = page.locator('#admin-themes-host');
  await expect(themes.getByRole('tab', { name: /Judgement/ })).toBeVisible();
  await expect(themes.getByRole('button', { name: 'Judge now' })).toBeVisible();

  // The provider panel (administrator area): script text with parameters.
  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });
  const providers = page.locator('#admin-providers-host');
  await expect(providers.getByRole('heading', { level: 2, name: /LLM and provider management/ })).toBeVisible();
  await expect(providers.getByRole('button', { name: 'Save the settings' })).toBeVisible();

  // The inline catalog of a console page has the script keys only; a page that is not in the administrator area has no keys of the administrator scripts.
  await page.goto('/account/profile', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#settings-account-info h2')).toContainText('Account information');
  const keys = await page.evaluate(() => Object.keys(JSON.parse(document.getElementById('claire-i18n-catalog').textContent)));
  expect(keys.some((key) => key.startsWith('console.js.account.'))).toBe(true);
  expect(keys.filter((key) => key.startsWith('console.') && !key.startsWith('console.js.'))).toEqual([]);
  expect(keys.filter((key) => key.startsWith('console.js.admin.'))).toEqual([]);

  // /moderation/kb also uses scope="console" without console.js.admin.* keys.
  await page.goto('/moderation/kb', { waitUntil: 'domcontentloaded' });
  const modKbKeys = await page.evaluate(() => Object.keys(JSON.parse(document.getElementById('claire-i18n-catalog').textContent)));
  expect(modKbKeys.filter((key) => key.startsWith('console.js.admin.'))).toEqual([]);

  // The language selector changes the console to Korean.
  await page.goto('/account/profile', { waitUntil: 'domcontentloaded' });
  await page.locator('#pref-account-locale').selectOption('ko');
  await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
  await expect(page.locator('#settings-account-info h2')).toContainText('계정 정보');
  await page.goto('/moderation/themes', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#admin-themes-host').getByRole('tab', { name: /판정과 임계값/ })).toBeVisible();

  // The stored language is a setting of the seed account, which other tests share: set it back to automatic.
  await page.goto('/account/profile', { waitUntil: 'domcontentloaded' });
  await page.locator('#pref-account-locale').selectOption('');
  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
});
