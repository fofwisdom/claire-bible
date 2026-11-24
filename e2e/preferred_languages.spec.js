// Preferred languages (ACCOUNT_PREFERRED_LANGUAGES_DESIGN.md) against the real E2E server: the administrator selects the server default language on /admin/server, the account that follows it shows it on /account/profile with its interface language, and /admin/providers shows the STT hints of it. The E2E server shares one database: the test removes the server default language at the end.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, expectNoRawKeys, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

async function clearServerLanguages(page) {
  const r = await page.request.put('/api/admin/server/languages', { data: { languages: null }, headers: { Origin: new URL(page.url()).origin } });
  expect(r.ok()).toBeTruthy();
}

test('server default language: select it on /admin/server, follow it on the profile, and remove it', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1360, height: 900 });
  await authenticateOwner(page, 'languages01');
  try {
    await page.goto('/admin/server', { waitUntil: 'domcontentloaded' });
    const card = page.locator('#server-languages');
    await expect(card.locator('[data-server-languages-state]')).toHaveAttribute('data-server-languages-state', 'none');
    // Add Japanese and Korean, move Korean to the first place, and save: the page loads again.
    await card.locator('[data-lang-input]').fill('ja');
    await card.locator('[data-lang-add]').click();
    await card.locator('[data-lang-input]').fill('ko');
    await card.locator('[data-lang-add]').click();
    const tags = () => card.locator('li[data-lang]').evaluateAll(els => els.map(el => el.dataset.lang));
    expect(await tags()).toEqual(['ja', 'ko', 'en']);
    await card.locator('li[data-lang="ko"] [data-lang-up]').click();
    expect(await tags()).toEqual(['ko', 'ja', 'en']);
    // A wrong code is refused next to the buttons.
    await card.locator('[data-lang-input]').fill('ko!');
    await card.locator('[data-lang-add]').click();
    await expect(card.locator('[data-language-result]')).toHaveAttribute('data-state', 'error');
    await card.locator('[data-lang-input]').fill('');
    await Promise.all([page.waitForNavigation(), card.locator('button[type="submit"]').click()]);
    await expect(page.locator('#server-languages [data-server-languages-state]')).toHaveAttribute('data-server-languages-state', 'selected');
    expect(await page.locator('#server-languages li[data-lang]').evaluateAll(els => els.map(el => el.dataset.lang))).toEqual(['ko', 'ja', 'en']);
    // The names come from the browser (Intl.DisplayNames) in the interface language.
    await expect(page.locator('#server-languages [data-lang-name="ja"]')).toHaveText('일본어');

    // The account follows it: Korean interface, the state and the list on the profile card.
    await page.goto('/account/profile', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
    await expect(page.locator('#settings-languages [data-languages-state]')).toHaveAttribute('data-languages-state', 'server');
    expect(await page.locator('#settings-languages li[data-lang]').evaluateAll(els => els.map(el => el.dataset.lang))).toEqual(['ko', 'ja', 'en']);
    await expectNoRawKeys(page);

    // The STT card of /admin/providers shows the hints of the list.
    await page.goto('/admin/providers#prov-stt-form', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#prov-stt-order [data-stt-order="stt"]')).toContainText('ko, ja, en');
  } finally {
    await clearServerLanguages(page);
  }
  await page.goto('/admin/server', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#server-languages [data-server-languages-state]')).toHaveAttribute('data-server-languages-state', 'none');
  expect(errors).toEqual([]);
});
