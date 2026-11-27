const { test, expect } = require('@playwright/test');

// Application bar (docs/implementation/WEBUI.md §2): the workspace, /syndication, and the console have one bar with the same structure and rules.
// A login token of the seed database is for one login. Each test has its own token (e2e/seed_db.py).
const login = (n) => `/?t=e2e-appbar${n}-token-0123456789abcde`;
const PAGES = [
  { name: 'workspace', path: '/', currentPattern: /^\/@e2eadmin\/$/ },  // single mode: "/" gives 302 to /@{username}/ (KNOWLEDGE_FEDERATION_DESIGN.md Section 15)
  { name: 'syndication', path: '/syndication', currentPattern: /^\/syndication$/ },
  { name: 'console', path: '/settings/reader', currentPattern: /^\/(?:admin|settings)$/ },
  { name: 'admin', path: '/admin/server', currentPattern: /^\/(?:admin|settings)$/ },
];

async function barFacts(page) {
  return page.evaluate(() => {
    const bar = document.querySelector('header.appbar');
    const style = getComputedStyle(bar);
    const brand = bar.querySelector('.bar-brand-group a.brand');
    const navLinks = Array.from(bar.querySelectorAll('.pub-appbar-nav a')).map((a) => ({
      href: new URL(a.href).pathname,
      text: a.textContent.trim(),
      left: Math.round(a.getBoundingClientRect().left),
    }));
    const paletteEl = bar.querySelector('.appbar-center #palettetrigger');
    const pickerEl = bar.querySelector('.appbar-center .theme-picker-wrap');
    const current = bar.querySelector('.appbar-end a[aria-current="page"]');
    const visibleEndOrder = ['#queuebtn', '#homelinkbtn', '#syndlink', '#hamburger-btn'].map((sel) => {
      const el = bar.querySelector(`.appbar-end ${sel}`);
      if (!el || el.hidden) return null;
      const r = el.getBoundingClientRect();
      return { sel, left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width) };
    });
    const notifyEl = bar.querySelector('.appbar-end #notifybtn');
    const notifyLeft = notifyEl && !notifyEl.hidden ? Math.round(notifyEl.getBoundingClientRect().left) : null;
    const visibleTopLogout = Array.from(bar.querySelectorAll('form[action="/logout"], a[href="/logout"]')).filter((el) => !el.closest('#pop-hamburger-menu') && el.offsetParent !== null).length;
    return {
      height: Math.round(bar.getBoundingClientRect().height),
      background: style.backgroundColor,
      brandText: brand ? brand.textContent.trim() : null,
      brandPath: brand ? new URL(brand.href).pathname : null,
      brandLeft: brand ? Math.round(brand.getBoundingClientRect().left) : -1,
      navLinks,
      paletteLeft: paletteEl ? Math.round(paletteEl.getBoundingClientRect().left) : -1,
      pickerLeft: pickerEl && getComputedStyle(pickerEl).display !== 'none' ? Math.round(pickerEl.getBoundingClientRect().left) : null,
      listToggleCount: document.querySelectorAll('#listtoggle').length,
      paletteVisible: !!paletteEl && getComputedStyle(paletteEl).display !== 'none',
      authStateCount: document.querySelectorAll('#authstate').length,
      visibleTopLogout,
      visibleEndOrder,
      notifyLeft,
      syndicationLinks: bar.querySelectorAll('.appbar-end > a[href^="/syndication"]').length,
      syndIcon: bar.querySelector('.appbar-end #syndlink')?.getAttribute('data-icon') || null,
      queueIcon: bar.querySelector('.appbar-end #queuebtn [data-icon]')?.getAttribute('data-icon') || null,
      menuSyndIcon: document.querySelector('#pop-hamburger-menu #menusyndlink [data-icon]')?.getAttribute('data-icon') || null,
      homeLinks: bar.querySelectorAll('.appbar-end > a#homelinkbtn').length,
      hamburgerConsoleLinks: bar.querySelectorAll('#pop-hamburger-menu a#consolelinkbtn').length,
      hamburgerLogoutForms: bar.querySelectorAll('#pop-hamburger-menu form[action="/logout"]').length,
      topThemeSwitchCount: bar.querySelectorAll('.appbar-end > #themebtn').length,
      menuThemeSwitch: !!bar.querySelector('#pop-hamburger-menu #themebtn'),
      currentPath: current ? new URL(current.href).pathname : null,
      inputs: bar.querySelectorAll('input').length,
    };
  });
}

test('the three pages share one app bar, palette trigger, right-to-left icons, hamburger menu, and ingest popups', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 800 });
  await page.goto(login(1), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#syndlink')).toBeVisible();
  await expect(page.locator('#queuebtn')).toBeVisible();
  const facts = [];
  for (const target of PAGES) {
    await page.goto(target.path, { waitUntil: 'domcontentloaded' });
    await expect(page.locator('header.appbar')).toHaveCount(1);
    await expect(page.locator('#syndlink')).toBeVisible();
    await expect(page.locator('#queuebtn')).toBeVisible();
    const fact = await barFacts(page);
    expect(fact.brandText, target.name).toBe('Claire Bible');
    // In single mode the workspace is at /@{username}/ (KNOWLEDGE_FEDERATION_DESIGN.md Section 15): its brand goes to the home of its knowledge base. The other pages link to "/", which gives 302 to the same place.
    expect(fact.brandPath, target.name).toBe(target.name === 'workspace' ? '/@e2eadmin/' : '/');
    expect(fact.listToggleCount, target.name).toBe(0);
    expect(fact.navLinks.map((n) => [n.href, n.text]), target.name).toEqual([
      ['/', '홈'],
      ['/themes', '테마'],
      ['/directory', '디렉터리'],
      ['/about', '소개'],
    ]);
    const leftPositions = [fact.brandLeft, ...fact.navLinks.map((n) => n.left), fact.paletteLeft];
    if (fact.pickerLeft !== null) leftPositions.push(fact.pickerLeft);
    for (let i = 1; i < leftPositions.length; i += 1) {
      expect(leftPositions[i], `${target.name} left-to-right order[${i}]`).toBeGreaterThan(leftPositions[i - 1]);
    }
    expect(fact.paletteVisible, target.name).toBe(true);
    expect(fact.authStateCount, target.name).toBe(0);
    expect(fact.visibleTopLogout, target.name).toBe(0);
    expect(fact.homeLinks, target.name).toBe(1);
    expect(fact.syndicationLinks, target.name).toBe(1);
    expect(fact.syndIcon, target.name).toBe('rss');
    expect(fact.queueIcon, target.name).toBe('layers');
    expect(fact.menuSyndIcon, target.name).toBe('rss');
    expect(fact.hamburgerConsoleLinks, target.name).toBe(1);
    expect(fact.hamburgerLogoutForms, target.name).toBe(1);
    expect(fact.topThemeSwitchCount, target.name).toBe(0);
    expect(fact.menuThemeSwitch, target.name).toBe(true);
    expect(fact.currentPath, target.name).toMatch(target.currentPattern);
    expect(fact.inputs, target.name).toBe(0);

    // Order from left to right along .appbar-end: (notifybtn ->) queuebtn -> homelinkbtn -> syndlink -> hamburger-btn (rightmost).
    expect(fact.visibleEndOrder.every((item) => item && item.width > 0), target.name).toBe(true);
    if (fact.notifyLeft !== null) {
      expect(fact.visibleEndOrder[0].left, `${target.name} #queuebtn right of #notifybtn`).toBeGreaterThan(fact.notifyLeft);
    }
    for (let i = 1; i < fact.visibleEndOrder.length; i += 1) {
      expect(fact.visibleEndOrder[i].left, `${target.name} ${fact.visibleEndOrder[i].sel}`).toBeGreaterThan(fact.visibleEndOrder[i - 1].left);
    }

    // Command palette opens from the top bar trigger on every page.
    await page.locator('#palettetrigger').click();
    await expect(page.locator('#cmdpalette')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('#cmdpalette')).toBeHidden();

    // Hamburger menu exposes the theme toggle and the common bottom section from top to bottom: syndication -> moderation -> console -> sign-out.
    await page.locator('#hamburger-btn').click();
    await expect(page.locator('#pop-hamburger-menu')).toBeVisible();
    await expect(page.locator('#pop-hamburger-menu #themebtn')).toBeVisible();
    await expect(page.locator('#menusyndlink')).toBeVisible();
    await expect(page.locator('#moderationlinkbtn')).toBeVisible();
    await expect(page.locator('#consolelinkbtn')).toBeVisible();
    await expect(page.locator('#menu-logout-form')).toBeVisible();
    const menuBottomOrder = await page.evaluate(() => ['#menusyndlink', '#moderationlinkbtn', '#consolelinkbtn', '#menu-logout-form'].map((sel) => Math.round(document.querySelector(sel).getBoundingClientRect().top)));
    for (let i = 1; i < menuBottomOrder.length; i += 1) {
      expect(menuBottomOrder[i], `${target.name} menu order`).toBeGreaterThan(menuBottomOrder[i - 1]);
    }
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
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#pop-hamburger-menu #themebtn')).toBeVisible();
  await page.locator('#pop-hamburger-menu #themebtn').click();
  const after = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  expect(after).not.toBe(before);
  for (const path of ['/syndication', '/']) {
    await page.goto(path, { waitUntil: 'domcontentloaded' });
    expect(await page.evaluate(() => document.documentElement.getAttribute('data-theme'))).toBe(after);
  }
});

test('a narrow screen keeps top bar controls accessible without overflow across all three pages', async ({ page }) => {
  await page.goto(login(3), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#syndlink')).toBeVisible();
  await expect(page.locator('#queuebtn')).toBeVisible();
  for (const vp of [{ width: 900, height: 800 }, { width: 375, height: 812 }]) {
    await page.setViewportSize(vp);
    for (const target of PAGES) {
      await page.goto(target.path, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('header.appbar a.brand')).toBeVisible();
      await expect(page.locator('#listtoggle')).toHaveCount(0);
      await expect(page.locator('header.appbar .pub-appbar-nav a')).toHaveCount(4);
      await expect(page.locator('#palettetrigger')).toBeVisible();
      await expect(page.locator('#queuebtn')).toBeVisible();
      await expect(page.locator('#homelinkbtn')).toBeVisible();
      await expect(page.locator('#syndlink')).toBeVisible();
      await expect(page.locator('#hamburger-btn')).toBeVisible();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow, `${target.name} ${vp.width}x${vp.height}`).toBeLessThanOrEqual(0);
    }
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
    provider: 'gemini',
    model: 'gemini-3.8-flash-medium',
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
  await expect(page.locator('#ingest-effort-hint')).toContainText('gemini-3.8-flash-medium');
  await context.close();
});
