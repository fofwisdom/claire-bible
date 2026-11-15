// Line icons of the pages other than the home view (HOME_OVERVIEW_AND_CONTEXT_PANEL_DESIGN.md, HD4; static/js/icons.js).
const { test, expect } = require('@playwright/test');

// Characters that a browser shows as a color pictograph.
const EMOJI = /\p{Extended_Pictographic}️|[\u{1F000}-\u{1FAFF}]/u;

test.describe('line icons', () => {
  test('the top bar and the menus of /syndication have line icons and no emoji', async ({ page }) => {
    await page.goto('/syndication', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#panel-kb')).toBeVisible();

    // Markup with `data-icon` gets an inline SVG that uses the text color.
    const prefIcon = page.locator('#top-pref-link svg.lineicon');
    await expect(prefIcon).toBeVisible();
    expect(await prefIcon.evaluate(el => getComputedStyle(el).stroke)).toBe(await page.locator('#top-pref-link').evaluate(el => getComputedStyle(el).color));
    expect(await prefIcon.evaluate(el => getComputedStyle(el).fill)).toBe('none');

    // The light and dark switch changes its icon through the `data-icon` attribute.
    const themeBtn = page.locator('#themebtn');
    const before = await themeBtn.getAttribute('data-icon');
    expect(['moon', 'sun']).toContain(before);
    await expect(themeBtn.locator('svg.lineicon')).toHaveCount(1);
    await themeBtn.click();
    await expect(themeBtn).toHaveAttribute('data-icon', before === 'moon' ? 'sun' : 'moon');
    await expect(themeBtn.locator('svg.lineicon')).toHaveCount(1);

    // The tree that a script draws has line icons too.
    await expect(page.locator('.tt-tree-item svg.lineicon').first()).toBeVisible();

    for (const selector of ['#top-pref-link', '#pop-main-menu']) {
      const texts = await page.locator(selector).evaluateAll(nodes => nodes.map(node => node.textContent));
      for (const text of texts) expect(text).not.toMatch(EMOJI);
    }
  });
});
