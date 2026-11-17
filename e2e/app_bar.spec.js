const { test, expect } = require('@playwright/test');

// Application bar (docs/implementation/WEBUI.md §2): the workspace, /syndication, and the console have one bar with the same structure and rules.
// A login token of the seed database is for one login. Each test has its own token (e2e/seed_db.py).
const login = (n) => `/?t=e2e-appbar${n}-token-0123456789abcde`;
const PAGES = [
  { name: 'workspace', path: '/', currentPattern: /^\/$/ },
  { name: 'syndication', path: '/syndication', currentPattern: /^\/syndication$/ },
  { name: 'console', path: '/settings/reader', currentPattern: /^\/(?:admin|settings)$/ },
];

async function barFacts(page) {
  return page.evaluate(() => {
    const bar = document.querySelector('header.appbar');
    const style = getComputedStyle(bar);
    const brand = bar.querySelector('.bar-brand-group a.brand');
    const current = bar.querySelector('.appbar-end a[aria-current="page"]');
    const visibleEndOrder = ['#themebtn', '#homelinkbtn', '#syndlink', '#hamburger-btn'].map((sel) => {
      const el = bar.querySelector(`.appbar-end ${sel}`);
      if (!el || el.hidden) return null;
      const r = el.getBoundingClientRect();
      return { sel, left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width) };
    });
    const visibleTopLogout = Array.from(bar.querySelectorAll('form[action="/logout"], a[href="/logout"]')).filter((el) => !el.closest('#pop-hamburger-menu') && el.offsetParent !== null).length;
    return {
      height: Math.round(bar.getBoundingClientRect().height),
      background: style.backgroundColor,
      brandText: brand ? brand.textContent.trim() : null,
      brandPath: brand ? new URL(brand.href).pathname : null,
      paletteVisible: !!bar.querySelector('.appbar-center #palettetrigger') && getComputedStyle(bar.querySelector('#palettetrigger')).display !== 'none',
      authStateCount: document.querySelectorAll('#authstate').length,
      visibleTopLogout,
      visibleEndOrder,
      syndicationLinks: bar.querySelectorAll('.appbar-end > a[href^="/syndication"]').length,
      homeLinks: bar.querySelectorAll('.appbar-end > a#homelinkbtn').length,
      hamburgerConsoleLinks: bar.querySelectorAll('#pop-hamburger-menu a#consolelinkbtn').length,
      hamburgerLogoutForms: bar.querySelectorAll('#pop-hamburger-menu form[action="/logout"]').length,
      themeSwitch: !!bar.querySelector('.appbar-end #themebtn'),
      currentPath: current ? new URL(current.href).pathname : null,
      inputs: bar.querySelectorAll('input').length,
    };
  });
}

test('the three pages share one app bar, palette trigger, right-to-left icons, hamburger menu, and ingest popups', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 800 });
  await page.goto(login(1), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#syndlink')).toBeVisible();
  const facts = [];
  for (const target of PAGES) {
    await page.goto(target.path, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('header.appbar')).toHaveCount(1);
    await expect(page.locator('#syndlink')).toBeVisible();
    const fact = await barFacts(page);
    expect(fact.brandText, target.name).toBe('Claire Bible');
    expect(fact.brandPath, target.name).toBe('/');
    expect(fact.paletteVisible, target.name).toBe(true);
    expect(fact.authStateCount, target.name).toBe(0);
    expect(fact.visibleTopLogout, target.name).toBe(0);
    expect(fact.homeLinks, target.name).toBe(1);
    expect(fact.syndicationLinks, target.name).toBe(1);
    expect(fact.hamburgerConsoleLinks, target.name).toBe(1);
    expect(fact.hamburgerLogoutForms, target.name).toBe(1);
    expect(fact.themeSwitch, target.name).toBe(true);
    expect(fact.currentPath, target.name).toMatch(target.currentPattern);
    expect(fact.inputs, target.name).toBe(0);

    // Order from left to right along .appbar-end: themebtn -> homelinkbtn -> syndlink -> hamburger-btn (rightmost).
    expect(fact.visibleEndOrder.every((item) => item && item.width > 0), target.name).toBe(true);
    for (let i = 1; i < fact.visibleEndOrder.length; i += 1) {
      expect(fact.visibleEndOrder[i].left, `${target.name} ${fact.visibleEndOrder[i].sel}`).toBeGreaterThan(fact.visibleEndOrder[i - 1].left);
    }

    // Command palette opens from the top bar trigger on every page.
    await page.locator('#palettetrigger').click();
    await expect(page.locator('#cmdpalette')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#cmdpalette')).toBeHidden();

    // Hamburger menu exposes console link and sign-out action.
    await page.locator('#hamburger-btn').click();
    await expect(page.locator('#pop-hamburger-menu')).toBeVisible();
    await expect(page.locator('#consolelinkbtn')).toBeVisible();
    await expect(page.locator('#menu-logout-form')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#pop-hamburger-menu')).toBeHidden();

    facts.push(fact);
  }
  expect(new Set(facts.map((fact) => fact.height)).size, JSON.stringify(facts.map((fact) => fact.height))).toBe(1);
  expect(new Set(facts.map((fact) => fact.background)).size).toBe(1);

  // Ingest button opens the shared dropdown and opens modal popups identically on / and /syndication.
  for (const path of ['/', '/syndication']) {
    await page.goto(path, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#addbtn')).toBeVisible();
    await page.locator('#addbtn').click();
    await expect(page.locator('#pop-ingest-menu')).toBeVisible();
    await page.locator('#ingest-opt-new').click();
    await expect(page.locator('dialog#modal-ingest')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('dialog#modal-ingest')).toBeHidden();

    await page.locator('#addbtn').click();
    await page.locator('#ingest-opt-reingest').click();
    await expect(page.locator('dialog#modal-reingest')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('dialog#modal-reingest')).toBeHidden();

    await page.locator('#addbtn').click();
    await page.locator('#ingest-opt-regen').click();
    await expect(page.locator('dialog#modal-regen')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('dialog#modal-regen')).toBeHidden();
  }
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

test('a narrow screen keeps top bar controls accessible without overflow across all three pages', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto(login(3), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#syndlink')).toBeVisible();
  for (const target of PAGES) {
    await page.goto(target.path, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('header.appbar a.brand')).toBeVisible();
    await expect(page.locator('#palettetrigger')).toBeVisible();
    await expect(page.locator('#homelinkbtn')).toBeVisible();
    await expect(page.locator('#syndlink')).toBeVisible();
    await expect(page.locator('#hamburger-btn')).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, target.name).toBeLessThanOrEqual(0);
  }
});

test('ingest modals expose only selectable provider effort options and fixed models hide unsupported choices', async ({ browser }) => {
  const context = await browser.newContext({ serviceWorkers: 'block', locale: 'ko-KR' });
  const page = await context.newPage();
  let effortSpec = {
    provider: 'gemini',
    model: 'gemini-4-argon',
    options: ['low', 'medium', 'high'],
    default_effort: 'medium',
    fixed: false,
    fixed_effort: '',
    tier_max_effort: '',
  };
  await page.route(/\/stats(\?|$)/, async (route) => {
    const resp = await route.fetch();
    const json = await resp.json();
    if (Object.prototype.hasOwnProperty.call(json, 'ingest_effort')) {
      json.ingest_effort = effortSpec;
    }
    await route.fulfill({
      status: resp.status(),
      contentType: 'application/json',
      body: JSON.stringify(json),
    });
  });

  await page.goto(login(4), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#addbtn')).toBeVisible();

  await page.locator('#addbtn').click();
  await page.locator('#ingest-opt-new').click();
  await expect(page.locator('dialog#modal-ingest')).toBeVisible();
  await expect.poll(() =>
    page.locator('#ingest-effort-grid input[name="ingest-effort"]').evaluateAll((els) => els.map((e) => e.value))
  ).toEqual(['', 'low', 'medium', 'high']);
  await page.keyboard.press('Escape');

  await page.locator('#addbtn').click();
  await page.locator('#ingest-opt-reingest').click();
  await expect(page.locator('dialog#modal-reingest')).toBeVisible();
  await expect.poll(() =>
    page.locator('#reingest-effort-grid input[name="reingest-effort"]').evaluateAll((els) => els.map((e) => e.value))
  ).toEqual(['', 'low', 'medium', 'high']);
  await page.keyboard.press('Escape');

  await page.locator('#addbtn').click();
  await page.locator('#ingest-opt-regen').click();
  await expect(page.locator('dialog#modal-regen')).toBeVisible();
  await expect.poll(() =>
    page.locator('#regen-effort-grid input[name="regen-effort"]').evaluateAll((els) => els.map((e) => e.value))
  ).toEqual(['', 'low', 'medium', 'high']);
  await page.keyboard.press('Escape');

  effortSpec = {
    provider: 'antigravity',
    model: 'claude-opus-5-5-medium',
    options: [],
    default_effort: 'medium',
    fixed: true,
    fixed_effort: 'medium',
    tier_max_effort: '',
  };
  await page.goto('/syndication', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#addbtn')).toBeVisible();
  await page.locator('#addbtn').click();
  await page.locator('#ingest-opt-new').click();
  await expect(page.locator('dialog#modal-ingest')).toBeVisible();
  await expect.poll(() =>
    page.locator('#ingest-effort-grid input[name="ingest-effort"]').evaluateAll((els) => els.map((e) => e.value))
  ).toEqual(['']);
  await expect(page.locator('#ingest-effort-hint')).toContainText('claude-opus-5-5-medium');
  await context.close();
});
