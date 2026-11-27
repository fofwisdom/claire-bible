// Public /about page, shared Gemini Neon pipeline interactions, public header /about navigation, and #modal-ingest rendition 3-template help.
const { test, expect } = require('@playwright/test');
const { expectNoRawKeys, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

const OWNER_COOKIE_TOKEN = 'e2e-owner-token-0123456789abcdef';
const ABOUT_RAW_KEY = /(?:^|[\s"'(>:])((?:about|pipeline\.neon|lang\.name)\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)/;

async function expectNoAboutRawKeys(page) {
  await expectNoRawKeys(page);
  const text = await page.evaluate(() => document.body.innerText || '');
  const found = ABOUT_RAW_KEY.exec(text);
  expect(found ? found[1] : null, 'raw about/pipeline i18n key').toBeNull();
}

async function expectZeroOverflow(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, `${label} horizontal overflow`).toBeLessThanOrEqual(1);
}

test('public /about works anonymously across desktop/mobile and light/dark themes with Gemini Neon interactions', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1360, height: 900 });

  // Navigate to /about via the shared public header link from /themes and verify /reference is excluded from public header.
  await page.goto('/themes', { waitUntil: 'domcontentloaded' });
  const headerAboutLink = page.locator('header.appbar .pub-appbar-nav a[href="/about"]');
  await expect(headerAboutLink).toBeVisible();
  await expect(page.locator('header.appbar a[href="/reference"]')).toHaveCount(0);
  await headerAboutLink.click();
  await expect(page).toHaveURL(/\/about$/);

  // Light theme on desktop 1360x900
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('#about-top')).toBeVisible();
  await expect(page.locator('#about-top .about-meta-strip')).toContainText('지식 관리자');
  await expect(page.locator('#pipeline-graph .p4-theatre')).toBeVisible();
  await expect(page.locator('#pipeline-graph [data-jev-pill], #pipeline-graph [data-jev-legend]')).toHaveCount(0);
  await expectNoAboutRawKeys(page);
  await expectZeroOverflow(page, 'desktop light');

  // 5-stage Gemini Neon node switching + 서버 API / 에이전트 클라이언트 toggle + Beat 04 5-Gate meter + #p4-motion-btn (no aria-pressed)
  const motionBtn = page.locator('#p4-motion-btn');
  await expect(motionBtn).toBeVisible();
  await expect(motionBtn).not.toHaveAttribute('aria-pressed', /.*/);
  await expect(motionBtn).toContainText('흐름 정지');
  await motionBtn.click();
  await expect(page.locator('#pipeline-graph .p4-theatre')).toHaveClass(/p4-paused/);
  await expect(motionBtn).toContainText('흐름 재생');
  await motionBtn.click();
  await expect(page.locator('#pipeline-graph .p4-theatre')).not.toHaveClass(/p4-paused/);

  await expect(page.locator('#pipeline-graph .p4-node')).toHaveCount(5);
  await page.locator('#pipeline-graph .p4-node[data-p4-stage="2"]').click();
  await expect(page.locator('#pipeline-graph .p4-node[data-p4-stage="2"]')).toHaveAttribute('aria-current', 'step');
  await page.locator('#pipeline-graph [data-p4-rend="academic"]').click();
  await expect(page.locator('#pipeline-graph [data-p4-rend="academic"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#pipeline-graph .p4-pre')).toContainText('원문 표');

  await page.locator('#pipeline-graph [data-p4-mode="client"]').click();
  await expect(page.locator('#pipeline-graph [data-p4-mode="client"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#pipeline-graph .p4-nonce-frame')).toContainText('untrusted_source_document');
  await page.locator('#pipeline-graph [data-p4-mode="provider"]').click();

  await page.locator('#pipeline-graph .p4-node[data-p4-stage="3"]').click();
  await expect(page.locator('#pipeline-graph .p4-gate-row')).toHaveCount(5);
  await page.locator('#pipeline-graph [data-p4-gate-preset="auto"]').click();
  await expect(page.locator('#pipeline-graph [data-p4-gate-result]')).toContainText('Gate 3');
  await page.locator('#pipeline-graph [data-p4-gate-preset="conflict"]').click();
  await expect(page.locator('#pipeline-graph [data-p4-gate-result]')).toContainText('Gate 2');
  await page.locator('#pipeline-graph [data-p4-gate-preset="border"]').click();
  await expect(page.locator('#pipeline-graph [data-p4-gate-result]')).toContainText('Gate 4');

  // Always-visible supported sources table and footer API 문서 link (without repeating curator)
  await expect(page.locator('#about-sources table tbody tr')).toHaveCount(6);
  await expect(page.locator('footer.about-footer a[href="/reference"]')).toContainText('API 문서');
  await expect(page.locator('footer.about-footer')).not.toContainText('지식 관리자');

  // Dark theme on desktop 1360x900
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#pop-hamburger-menu #themebtn')).toBeVisible();
  await page.locator('#pop-hamburger-menu #themebtn').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expectZeroOverflow(page, 'desktop dark');

  // Mobile 390x844 in dark and light themes
  await page.setViewportSize({ width: 390, height: 844 });
  await expectZeroOverflow(page, 'mobile dark');
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#pop-hamburger-menu #themebtn')).toBeVisible();
  await page.locator('#pop-hamburger-menu #themebtn').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expectZeroOverflow(page, 'mobile light');

  expect(errors).toEqual([]);
});

test('#modal-ingest displays rendition template 3-type policy help without altering template controls', async ({ browser, baseURL }) => {
  const context = await browser.newContext({ serviceWorkers: 'block', locale: 'ko-KR' });
  await context.addCookies([{ name: 'claire_session', value: OWNER_COOKIE_TOKEN, url: baseURL }]);
  const page = await context.newPage();
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1360, height: 900 });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#addbtn')).toBeVisible();
  await page.locator('#addbtn').click();
  await page.locator('#pop-ingest-menu [data-ingest-cmd="ingest"]').click();

  const modal = page.locator('#modal-ingest');
  await expect(modal).toBeVisible();
  const policy = modal.locator('.ingest-rendition-policy');
  await expect(policy).toBeVisible();
  await expect(policy.locator('#ingest-rendition-policy-title')).toContainText('정리본 템플릿 3종 안내');
  await expect(policy.locator('li')).toHaveCount(3);
  await expect(policy).toContainText('기본');
  await expect(policy).toContainText('간결');
  await expect(policy).toContainText('학술');
  await expect(modal.locator('label[for="ingrendition-standard"]')).toHaveText('기본');
  await expect(modal.locator('label[for="ingrendition-concise"]')).toHaveText('간결');
  await expect(modal.locator('label[for="ingrendition-academic"]')).toHaveText('학술');
  await expectNoAboutRawKeys(page);

  await context.close();
  expect(errors).toEqual([]);
});
