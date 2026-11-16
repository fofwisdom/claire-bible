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
  await expect(page.locator('#authstate')).toHaveText('Authenticated');

  // The application bar and the view menu: no Korean text (the theme name is account data and is not in these parts).
  const texts = await controlTexts(page, '#centerhead, .appbar-end, #moremenu, #listswitch');
  expect(texts.length).toBeGreaterThan(20);
  for (const text of texts) {
    expect(HANGUL.test(text), text.slice(0, 80)).toBe(false);
  }

  // The ingestion panel that app.js renders.
  await page.evaluate(() => openIngest());
  const panel = page.locator('#detailpane');
  await expect(panel.locator('h2')).toContainText('Ingest material');
  await expect(panel.getByRole('button', { name: 'Start the ingestion' })).toBeVisible();

  // The reader chrome that reader.js renders for a document.
  await page.evaluate(() => { selectDoc(allDocs[0].id); setCenterView('reader'); });
  await expect(page.locator('#reader .rsection').first()).toHaveText(/^(Summary|Detail|Summary and detail|Focus)$/);
});
