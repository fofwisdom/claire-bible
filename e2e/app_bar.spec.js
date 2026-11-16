const { test, expect } = require('@playwright/test');

// Application bar (docs/implementation/WEBUI.md §2): the workspace, /syndication, and the console have one bar with the same structure and rules.
// A login token of the seed database is for one login. Each test has its own token (e2e/seed_db.py).
const login = (n) => `/?t=e2e-appbar${n}-token-0123456789abcde`;
const PAGES = [
  { name: 'workspace', path: '/', current: null },
  { name: 'syndication', path: '/syndication', current: '/syndication' },
  { name: 'console', path: '/settings/reader', current: '/settings' },
];

async function barFacts(page) {
  return page.evaluate(() => {
    const bar = document.querySelector('header.appbar');
    const style = getComputedStyle(bar);
    const brand = bar.querySelector('.bar-brand-group a.brand');
    const current = bar.querySelector('.appbar-end a[aria-current="page"]');
    return {
      height: Math.round(bar.getBoundingClientRect().height),
      background: style.backgroundColor,
      brandText: brand ? brand.textContent.trim() : null,
      brandPath: brand ? new URL(brand.href).pathname : null,
      syndicationLinks: bar.querySelectorAll('.appbar-end a[href^="/syndication"]').length,
      // The console link of the workspace goes to /admin for an administrator and to /settings for other accounts.
      settingsLinks: bar.querySelectorAll('.appbar-end a[href^="/settings"], .appbar-end a[href^="/admin"]').length,
      themeSwitch: !!bar.querySelector('.appbar-end #themebtn'),
      currentPath: current ? new URL(current.href).pathname : null,
      inputs: bar.querySelectorAll('input').length,
    };
  });
}

test('the three pages have the same application bar', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 800 });
  await page.goto(login(1), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#syndlink')).toBeVisible();
  const facts = [];
  for (const target of PAGES) {
    await page.goto(target.path, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('header.appbar')).toHaveCount(1);
    const fact = await barFacts(page);
    expect(fact.brandText, target.name).toBe('Claire Bible');
    expect(fact.brandPath, target.name).toBe('/');
    expect(fact.syndicationLinks, target.name).toBe(1);
    expect(fact.settingsLinks, target.name).toBe(1);
    expect(fact.themeSwitch, target.name).toBe(true);
    expect(fact.currentPath, target.name).toBe(target.current);
    expect(fact.inputs, target.name).toBe(0);
    facts.push(fact);
  }
  expect(new Set(facts.map((fact) => fact.height)).size, JSON.stringify(facts.map((fact) => fact.height))).toBe(1);
  expect(new Set(facts.map((fact) => fact.background)).size).toBe(1);
});

test('the light and dark switch of one page applies to the other pages', async ({ page }) => {
  await page.goto(login(2), { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await page.goto('/settings/reader', { waitUntil: 'domcontentloaded' });
  const before = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  await page.locator('#themebtn').click();
  const after = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  expect(after).not.toBe(before);
  for (const path of ['/syndication', '/']) {
    await page.goto(path, { waitUntil: 'domcontentloaded' });
    expect(await page.evaluate(() => document.documentElement.getAttribute('data-theme'))).toBe(after);
  }
});

test('a narrow screen shows the bar and the link to /syndication', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(login(3), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#syndlink')).toBeVisible();
  await page.locator('#syndlink').click();
  await expect(page).toHaveURL(/\/syndication/);
  await expect(page.locator('header.appbar a.brand')).toBeVisible();
  await expect(page.locator('#btn-palette')).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});
