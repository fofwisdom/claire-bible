const { test, expect } = require('@playwright/test');

// The text of the knowledge base view of /syndication comes from the catalogs (static/locales/{en,ko}.json). The seed documents have Korean titles, thus the test reads only the controls of the page.
test.use({ locale: 'en-US' });

const HANGUL = /[가-힣]/;

test('the knowledge base view of /syndication shows English text for an English browser', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 800 });
  // A login token of the seed database is for one login. This test has its own token (e2e/seed_db.py).
  await page.goto('/?t=e2e-syndi18n-token-0123456789abcd', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await page.goto('/syndication?stage=ingest', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#headlines-container .tt-row-check').first()).toBeVisible();

  await expect(page.locator('#btn-refresh')).toContainText('Refresh');
  await expect(page.locator('#btn-mark-read')).toContainText('Mark as read');
  await expect(page.locator('#btn-sort-label')).toHaveText('Newest');
  await expect(page.locator('#btn-list-style-label')).toContainText('List');
  await expect(page.locator('#active-stream-title')).toHaveText('All ingested documents');
  await expect(page.locator('#stream-search')).toHaveAttribute('placeholder', /Search title/);

  for (const selector of ['.synd-toolbar', '#pane-tree summary', '#tree-specials', '#activity-ticker', '#pop-main-menu', '#pop-sort', '#pop-list-style', '#pop-article-actions']) {
    const texts = await page.locator(selector).evaluateAll((nodes) => nodes.map((node) => node.textContent));
    expect(texts.length, selector).toBeGreaterThan(0);
    for (const text of texts) expect(HANGUL.test(text), `${selector}: ${text.trim().slice(0, 80)}`).toBe(false);
  }

  // The reader controls of a document.
  await page.locator('#headlines-container [data-click-row]').first().click();
  await expect(page.locator('[data-target-tab="original"]')).toContainText('Source text');
  await expect(page.locator('[data-target-tab="ingested"]')).toContainText('Ingested text');
  await expect(page.locator('[data-open-dispatch]')).toContainText('Send to another theme');
});
