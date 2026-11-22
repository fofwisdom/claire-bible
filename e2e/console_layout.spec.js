const { test, expect } = require('@playwright/test');

test.use({ serviceWorkers: 'block' });

const CONSOLE_LAYOUT_TOKEN = 'e2e-console-layout-token-0123456789ab';

let ownerStorageState = null;

async function ensureOwnerState(browser) {
  if (ownerStorageState) return ownerStorageState;
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto(`/?t=${CONSOLE_LAYOUT_TOKEN}`, { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await expect(page.locator('#hamburger-btn')).toBeVisible();
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#consolelinkbtn')).toBeVisible();
  ownerStorageState = await ctx.storageState();
  await ctx.close();
  return ownerStorageState;
}

test('modern console layout supports document scroll, sticky sidebar, split rows, and collapsible groups across desktop viewports', async ({ browser }) => {
  const storageState = await ensureOwnerState(browser);
  const context = await browser.newContext({ storageState });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message));

  for (const vp of [{ width: 1440, height: 900 }, { width: 1280, height: 620 }]) {
    await page.setViewportSize(vp);
    for (const path of ['/account/profile', '/settings/feeds', '/admin/server']) {
      await page.goto(path, { waitUntil: 'networkidle' });
      const metrics = await page.evaluate(() => {
        const html = document.documentElement;
        const body = document.body;
        const sidebar = document.getElementById('console-sidebar');
        const bodyOverflowY = getComputedStyle(body).overflowY;
        window.scrollTo(0, html.scrollHeight);
        const container = document.getElementById('console-section-body');
        const containerBottom = container ? container.getBoundingClientRect().bottom : 0;
        const sidebarTop = sidebar ? sidebar.getBoundingClientRect().top : -1;
        return {
          clientWidth: html.clientWidth,
          scrollWidth: html.scrollWidth,
          bodyOverflowY,
          containerBottom,
          innerHeight: window.innerHeight,
          sidebarTop,
        };
      });
      expect(metrics.scrollWidth, `${path} @ ${vp.width}x${vp.height} horizontal overflow`).toBeLessThanOrEqual(metrics.clientWidth);
      expect(metrics.bodyOverflowY).not.toBe('hidden');
      expect(metrics.containerBottom, `${path} @ ${vp.width}x${vp.height} scrolled bottom`).toBeLessThanOrEqual(metrics.innerHeight + 48);
      expect(metrics.sidebarTop).toBeGreaterThanOrEqual(36);
      expect(metrics.sidebarTop).toBeLessThanOrEqual(72);
    }
  }

  // Split rows on 1440x900
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/admin/server', { waitUntil: 'networkidle' });
  const splitGeom = await page.evaluate(() => {
    const split = document.querySelector('#console-section-body .is-split');
    const meta = split ? split.querySelector('.console-split-meta') : null;
    const ctrl = split ? split.querySelector('.console-split-ctrl') : null;
    return meta && ctrl ? { metaLeft: meta.getBoundingClientRect().left, ctrlLeft: ctrl.getBoundingClientRect().left } : null;
  });
  expect(splitGeom).not.toBeNull();
  expect(splitGeom.ctrlLeft).toBeGreaterThan(splitGeom.metaLeft);

  // Collapsible group accordion persistence & auto-expand active group
  await page.goto('/account/profile', { waitUntil: 'networkidle' });
  const agentsToggle = page.locator('[data-nav-group-toggle="ai"]');
  await agentsToggle.click();
  await expect(agentsToggle).toHaveAttribute('aria-expanded', 'false');
  await expect(page.locator('#console-grp-ai')).toBeHidden();
  await page.reload({ waitUntil: 'networkidle' });
  await expect(page.locator('[data-nav-group-toggle="ai"]')).toHaveAttribute('aria-expanded', 'false');

  await page.goto('/account/mcp', { waitUntil: 'networkidle' });
  await expect(page.locator('[data-nav-group-toggle="ai"]')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#console-grp-ai')).toBeVisible();

  expect(pageErrors).toEqual([]);
  await context.close();
});

test('console command palette opens with / shortcut and sidebar trigger button to jump between sections', async ({ browser }) => {
  const storageState = await ensureOwnerState(browser);
  const context = await browser.newContext({ storageState });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message));

  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/account/profile', { waitUntil: 'networkidle' });

  // Open with top-bar command palette trigger and close with Escape even while input is focused
  await expect(page.locator('#console-palette-trigger')).toHaveCount(0);
  await page.locator('#palettetrigger').click();
  const dialog = page.locator('#cmdpalette');
  await expect(dialog).toBeVisible();
  await page.locator('#cmdpalette-input').fill('test');
  await page.keyboard.press('Escape');
  await expect(dialog).not.toBeVisible();

  // Open with / shortcut and jump to /settings/feeds
  await page.keyboard.press('/');
  await expect(dialog).toBeVisible();
  await page.locator('#cmdpalette-input').fill('feeds');
  const feedOption = page.locator('#cmdpalette-list button.cmd-palette-row').first();
  await expect(feedOption).toBeVisible();
  await feedOption.click();
  await expect(page).toHaveURL(/\/settings\/feeds/);

  expect(pageErrors).toEqual([]);
  await context.close();
});

test('mobile viewport uses single-node drawer without duplicate links or horizontal overflow', async ({ browser }) => {
  const storageState = await ensureOwnerState(browser);
  const context = await browser.newContext({ storageState });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message));

  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto('/admin/accounts', { waitUntil: 'networkidle' });

  const layout = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
    sidebarInnerCount: document.querySelectorAll('#console-sidebar-inner').length,
    navLinks: document.querySelectorAll('a[data-nav-section]').length,
    uniqueNavHrefs: new Set(Array.from(document.querySelectorAll('a[data-nav-section]')).map((el) => el.getAttribute('href'))).size,
    tableLabelsCount: document.querySelectorAll('#console-section-body td[data-label]').length,
  }));
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
  expect(layout.sidebarInnerCount).toBe(1);
  expect(layout.navLinks).toBeGreaterThan(0);
  expect(layout.navLinks).toBe(layout.uniqueNavHrefs);

  await expect(page.locator('#palettetrigger')).toBeVisible();
  await expect(page.locator('#homelinkbtn')).toBeVisible();
  await expect(page.locator('#syndlink')).toBeVisible();
  await expect(page.locator('#hamburger-btn')).toBeVisible();
  await expect(page.locator('#console-mob-menu')).toBeVisible();
  await expect(page.locator('#console-sidebar')).toBeHidden();

  await page.locator('#console-mob-menu').click();
  const drawer = page.locator('#console-nav-drawer');
  await expect(drawer).toBeVisible();
  await expect(drawer.locator('a[data-nav-section="profile"]')).toBeVisible();

  await page.locator('#console-drawer-close').click();
  await expect(drawer).not.toBeVisible();

  // Re-open and navigate via drawer link
  await page.locator('#console-mob-menu').click();
  await expect(drawer).toBeVisible();
  await drawer.locator('a[data-nav-section="profile"]').click();
  await expect(page).toHaveURL(/\/account\/profile/);

  expect(pageErrors).toEqual([]);
  await context.close();
});

test('account settings switch for public sources saves the value through PATCH /api/me and keeps it after a reload', async ({ browser }) => {
  const storageState = await ensureOwnerState(browser);
  const context = await browser.newContext({ storageState });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', (err) => pageErrors.push(err.message));

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/account/profile', { waitUntil: 'networkidle' });
  const toggle = page.locator('#pref-personal-source-public');
  await expect(toggle).toBeVisible();
  await expect(page.locator('[data-source-public-hint]')).toBeVisible();
  await expect(toggle).toBeChecked();

  const patched = page.waitForResponse((r) => r.url().endsWith('/api/me') && r.request().method() === 'PATCH');
  await toggle.click();
  const response = await patched;
  expect(response.status()).toBe(200);
  expect((await response.json()).personal_source_public).toBe(false);
  await expect(page.locator('[data-source-public] [data-settings-result]')).toHaveAttribute('data-state', 'ok');

  await page.reload({ waitUntil: 'networkidle' });
  await expect(page.locator('#pref-personal-source-public')).not.toBeChecked();
  // Back to the default, so that a second run starts from the same state.
  const reverted = page.waitForResponse((r) => r.url().endsWith('/api/me') && r.request().method() === 'PATCH');
  await page.locator('#pref-personal-source-public').click();
  expect((await (await reverted).json()).personal_source_public).toBe(true);

  expect(pageErrors).toEqual([]);
  await context.close();
});
