const { test, expect } = require('@playwright/test');
const { expectNoRawKeys, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// Unused owner session token from e2e/seed_db.py, consumed once and shared via storageState.
const CONSISTENCY_TOKEN = 'e2e-pres-token-0123456789abcdef01';

const SETTINGS_ROUTES = [
  '/account/profile',
  '/account/security',
  '/account/invites',
  '/account/federation',
  '/account/providers',
  '/account/telegram',
  '/support/bundles',
  '/support/pipeline',
  '/account/backups',
  '/settings/kb',
  '/settings/reader',
  '/settings/feeds',
  '/account/mcp',
  '/account/webmcp',
];

const ADMIN_ROUTES = [
  '/admin/server',
  '/admin/federation',
  '/admin/accounts',
  '/admin/idp',
  '/admin/providers',
  '/admin/limits',
  '/admin/plugins',
  '/admin/audit',
  '/admin/mcp',
  '/admin/webmcp',
  '/admin/backups',
  '/admin/storage',
  '/laboratory/measurements',
  '/laboratory/shadows',
];

const MODERATION_ROUTES = [
  '/moderation/kb',
  '/moderation/oversight',
  '/moderation/themes',
];

const ALL_DESKTOP_ROUTES = [...SETTINGS_ROUTES, ...ADMIN_ROUTES, ...MODERATION_ROUTES];

const MOBILE_REPRESENTATIVE_ROUTES = [
  '/account/profile',
  '/account/invites',
  '/settings/feeds',
  '/settings/kb',
  '/admin/server',
  '/admin/providers',
  '/admin/accounts',
  '/moderation/kb',
  '/moderation/oversight',
  '/moderation/themes',
];

// /admin/providers has tabs (owner selection Q1 of PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md): the tabs replace the list of the page. /support/pipeline uses data-toc-tabs="1" for simultaneous tabs and ToC.
const EXPECT_TOC_ROUTES = new Set(['/account/profile', '/admin/server', '/support/pipeline']);

let ownerStorageState = null;

async function ensureOwnerState(browser) {
  if (ownerStorageState) return ownerStorageState;
  const ctx = await browser.newContext();
  const page = await ctx.newPage();
  await page.goto(`/?t=${CONSISTENCY_TOKEN}`, { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  ownerStorageState = await ctx.storageState();
  await ctx.close();
  return ownerStorageState;
}

async function waitForConsoleSettled(page) {
  await expect(page.locator('header.appbar')).toBeVisible();
  await expect(page.locator('#console-main')).toBeVisible();
  await expect(page.locator('.console-sec-head')).toBeVisible();
  await expect(page.locator('#console-section-body')).toBeVisible();
  await expect.poll(() => page.evaluate(() => Boolean(window.ClaireConsoleShell && typeof window.ClaireConsoleShell.enhance === 'function'))).toBe(true);
  // Wait for client panel fetches to populate visible dynamic hosts.
  await expect.poll(async () => page.evaluate(() => {
    const hosts = Array.from(document.querySelectorAll('#console-section-body [data-admin-panel-host], #console-section-body [data-settings-panel-host], #console-section-body [data-console-host]'));
    return hosts.every((host) => {
      if (host.closest('[hidden]')) return true;
      const children = Array.from(host.children);
      if (host.matches('[data-admin-panel-host], [data-settings-panel-host]') && children.length === 0) return false;
      return !(children.length === 1 && children[0].matches('p.hint'));
    });
  }), { timeout: 10_000 }).toBe(true);
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
}

async function captureAccountBaseline(page) {
  await page.goto('/account/profile', { waitUntil: 'networkidle' });
  await waitForConsoleSettled(page);
  const baseline = await page.evaluate(() => {
    const card = document.querySelector('#console-section-body .console-subcard');
    const accentBtn = document.querySelector('#console-section-body .pref-btn-accent');
    const heading = document.querySelector('#console-section-body .console-subcard h2');
    if (!card || !accentBtn || !heading) return null;
    const cardStyle = getComputedStyle(card);
    const btnStyle = getComputedStyle(accentBtn);
    const headingStyle = getComputedStyle(heading);
    return {
      borderRadius: cardStyle.borderRadius,
      borderColor: cardStyle.borderColor,
      backgroundColor: cardStyle.backgroundColor,
      accentBackgroundColor: btnStyle.backgroundColor,
      h2FontSize: headingStyle.fontSize,
      h2FontWeight: headingStyle.fontWeight,
    };
  });
  expect(baseline).not.toBeNull();
  return baseline;
}

async function assertRouteUiConsistency(page, routePath, baseline, opts) {
  const isMobile = Boolean(opts && opts.mobile);
  await page.goto(routePath, { waitUntil: 'networkidle' });
  await waitForConsoleSettled(page);

  if (isMobile) {
    await expect(page.locator('.console-mob-bar')).toBeVisible();
    await expect(page.locator('#console-mob-menu')).toBeVisible();
    await expect(page.locator('#console-sidebar')).toBeAttached();
    await expect(page.locator('#console-sidebar')).toBeHidden();
  } else {
    await expect(page.locator('#console-sidebar')).toBeVisible();
  }

  await expect(page.locator('.claire-switch')).toHaveCount(0);

  const report = await page.evaluate((base) => {
    const isVisible = (el) => {
      if (!el) return false;
      const st = getComputedStyle(el);
      if (st.display === 'none' || st.visibility === 'hidden') return false;
      if (el.closest('[hidden], dialog:not([open])')) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };

    const cardSelector = '.console-subcard, .card, .pref-card, .claire-card';
    const body = document.getElementById('console-section-body');
    const allCards = body ? Array.from(body.querySelectorAll(cardSelector)).filter(isVisible) : [];
    const topLevelCards = [];
    const nestedCards = [];
    for (const card of allCards) {
      const parentCard = card.parentElement ? card.parentElement.closest(cardSelector) : null;
      if (parentCard && body.contains(parentCard)) {
        nestedCards.push({ outer: parentCard.id || parentCard.className, inner: card.id || card.className });
      } else {
        topLevelCards.push(card);
      }
    }

    const cardMismatches = [];
    for (const card of topLevelCards) {
      const st = getComputedStyle(card);
      if (st.borderRadius !== base.borderRadius || st.borderColor !== base.borderColor || st.backgroundColor !== base.backgroundColor) {
        cardMismatches.push({
          id: card.id || card.className,
          borderRadius: st.borderRadius,
          borderColor: st.borderColor,
          backgroundColor: st.backgroundColor,
          expectedRadius: base.borderRadius,
          expectedColor: base.borderColor,
          expectedBackground: base.backgroundColor,
        });
      }
    }

    const primaryBtns = body ? Array.from(body.querySelectorAll('.pref-btn-accent, .claire-btn.pri')).filter(isVisible) : [];
    const btnMismatches = [];
    for (const btn of primaryBtns) {
      const st = getComputedStyle(btn);
      if (st.backgroundColor !== base.accentBackgroundColor) {
        btnMismatches.push({
          text: (btn.textContent || '').trim(),
          backgroundColor: st.backgroundColor,
          expected: base.accentBackgroundColor,
        });
      }
    }

    const visibleH2s = body ? Array.from(body.querySelectorAll('h2')).filter((h) => !h.classList.contains('sr-only') && !h.closest('[hidden], dialog, .sr-only') && isVisible(h)) : [];
    const uniqueH2Texts = new Set(visibleH2s.map((h) => (h.textContent || '').trim()).filter(Boolean));
    const h2Mismatches = [];
    for (const h of visibleH2s) {
      const st = getComputedStyle(h);
      if (st.fontSize !== base.h2FontSize || st.fontWeight !== base.h2FontWeight) {
        h2Mismatches.push({
          text: (h.textContent || '').trim(),
          fontSize: st.fontSize,
          fontWeight: st.fontWeight,
        });
      }
    }

    const layoutEl = document.getElementById('console-layout');
    const tocEl = document.getElementById('console-toc');
    const html = document.documentElement;
    return {
      nestedCards,
      cardMismatches,
      btnMismatches,
      h2Mismatches,
      uniqueH2Count: uniqueH2Texts.size,
      hasTocClass: Boolean(layoutEl && layoutEl.classList.contains('has-toc')),
      tocVisible: isVisible(tocEl),
      clientWidth: html.clientWidth,
      scrollWidth: html.scrollWidth,
    };
  }, baseline);

  expect(report.nestedCards, `${routePath} nested cards`).toEqual([]);
  expect(report.cardMismatches, `${routePath} card border consistency`).toEqual([]);
  expect(report.btnMismatches, `${routePath} primary button color`).toEqual([]);
  expect(report.h2Mismatches, `${routePath} h2 typography consistency`).toEqual([]);
  expect(report.scrollWidth, `${routePath} horizontal overflow`).toBeLessThanOrEqual(report.clientWidth);

  if (!isMobile) {
    if (EXPECT_TOC_ROUTES.has(routePath)) {
      expect(report.uniqueH2Count, `${routePath} expected 3+ visible h2 headings`).toBeGreaterThanOrEqual(3);
    }
    if (report.uniqueH2Count >= 3) {
      expect(report.hasTocClass, `${routePath} should have has-toc`).toBe(true);
      expect(report.tocVisible, `${routePath} should show sticky #console-toc`).toBe(true);
    } else {
      expect(report.hasTocClass, `${routePath} should not have has-toc`).toBe(false);
    }
  }

  await expectNoRawKeys(page, '#console-main');
}

test('desktop routes across settings, admin, and moderation share unified console UI baseline', async ({ browser }) => {
  test.setTimeout(180_000);
  const storageState = await ensureOwnerState(browser);
  const context = await browser.newContext({ storageState, viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = watchErrors(page);

  const baseline = await captureAccountBaseline(page);
  for (const routePath of ALL_DESKTOP_ROUTES) {
    await assertRouteUiConsistency(page, routePath, baseline, { mobile: false });
  }

  expect(errors).toEqual([]);
  await context.close();
});

test('mobile routes across settings, admin, and moderation render responsive shell with zero overflow', async ({ browser }) => {
  test.setTimeout(120_000);
  const storageState = await ensureOwnerState(browser);
  const context = await browser.newContext({ storageState, viewport: { width: 390, height: 800 } });
  const page = await context.newPage();
  const errors = watchErrors(page);

  await page.setViewportSize({ width: 1440, height: 900 });
  const baseline = await captureAccountBaseline(page);
  await page.setViewportSize({ width: 390, height: 800 });

  for (const routePath of MOBILE_REPRESENTATIVE_ROUTES) {
    await assertRouteUiConsistency(page, routePath, baseline, { mobile: true });
  }

  expect(errors).toEqual([]);
  await context.close();
});
