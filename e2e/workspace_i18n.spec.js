const { test, expect } = require('@playwright/test');

// The workspace (/) renders its text in the interface language (I18N design unit IP2-3): the server resolves the slots of index.html, and app.js and reader.js read the catalog keys.
// The seed documents and entities have Korean names, thus the test reads only the controls of the page.
test.use({ locale: 'en-US' });

const HANGUL = /[가-힣]/;

async function controlTexts(page, selector) {
  return page.locator(selector).evaluateAll((nodes) => nodes.flatMap((node) => {
    const values = [];
    for (const el of [node, ...node.querySelectorAll('*')]) {
      for (const name of ['title', 'aria-label', 'placeholder']) if (el.getAttribute(name)) values.push(el.getAttribute(name));
    }
    values.push(node.innerText || '');
    return values;
  }));
}

test('the workspace shows English text for an English browser', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 860 });
  // A login token of the seed database is for one login. This test has its own token (e2e/seed_db.py).
  await page.goto('/?t=e2e-wsi18n-token-0123456789abcdef', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

  await expect(page.locator('html')).toHaveAttribute('lang', 'en');
  await expect(page).toHaveTitle('Claire Bible — Home');
  await expect(page.locator('#centertab-reader .btn-label')).toHaveText('Text');
  await expect(page.locator('#authstate')).toHaveCount(0);
  await expect(page.locator('#palettetrigger .palette-trigger-label')).toHaveText('Search · Go · Commands');

  // The application bar, hamburger menu, ingest menu, and view controls: no Korean text (the theme name is account data and is not in these parts).
  const texts = await controlTexts(page, '#centerhead, .appbar-end, #pop-hamburger-menu, #pop-ingest-menu, #listswitch');
  expect(texts.length).toBeGreaterThan(20);
  for (const text of texts) {
    expect(HANGUL.test(text), text.slice(0, 80)).toBe(false);
  }

  // The unified ingestion modal dialog that ClaireIngestDialogs + app.js render.
  await page.evaluate(() => openIngest());
  const modal = page.locator('dialog#modal-ingest');
  await expect(modal).toBeVisible();
  await expect(modal.locator('h2')).toContainText('Ingest material');
  await expect(modal.getByRole('button', { name: 'Start the ingestion' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden();

  // The reader chrome that reader.js renders for a document.
  await page.evaluate(() => { selectDoc(allDocs[0].id); setCenterView('reader'); });
  await expect(page.locator('#reader .rsection').first()).toHaveText(/^(Summary|Detail|Summary and detail|Focus)$/);
});
