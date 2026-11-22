const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('English /settings/mcp provides one-click Antigravity quick setup prompt copy and English GUI guide link', async ({ browser, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const context = await browser.newContext({ locale: 'en-US' });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
  const page = await context.newPage();
  const errors = watchErrors(page);

  await authenticateOwner(page, 'mcpgui001');
  await page.goto('/settings/mcp', { waitUntil: 'domcontentloaded' });

  const promptEl = page.locator('#pref-mcp-antigravity-prompt');
  await expect(promptEl).toBeVisible();
  const expectedMcpUrl = `${origin}/mcp`;
  await expect(promptEl).toContainText(expectedMcpUrl);
  await expect(promptEl).toContainText('https://github.com/fofwisdom/claire-agents');

  const guideLink = page.locator('#pref-mcp-antigravity-guide');
  await expect(guideLink).toHaveAttribute('href', 'https://github.com/fofwisdom/claire-agents/blob/main/docs/antigravity.en.md');

  const advancedDetails = page.locator('#pref-mcp-marketplace-advanced');
  await expect(advancedDetails).toBeVisible();
  await expect(advancedDetails).not.toHaveAttribute('open', '');

  await page.locator('#pref-mcp-copy-antigravity-prompt').click();
  await expect(page.locator('#pref-toast')).toContainText('Copied to clipboard.');
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  const promptText = await promptEl.textContent();
  expect(copied).toBe(promptText);

  await expectNoRawKeys(page, '#panel-mcp');
  expect(errors).toEqual([]);
  await context.close();
});

test('Korean /settings/mcp provides localized Antigravity prompt and Korean GUI guide link', async ({ browser, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const context = await browser.newContext({ locale: 'ko-KR' });
  const page = await context.newPage();
  const errors = watchErrors(page);

  await authenticateOwner(page, 'mcpgui002');
  await page.goto('/settings/mcp', { waitUntil: 'domcontentloaded' });

  const guideLink = page.locator('#pref-mcp-antigravity-guide');
  await expect(guideLink).toHaveAttribute('href', 'https://github.com/fofwisdom/claire-agents/blob/main/docs/antigravity.ko.md');

  const promptEl = page.locator('#pref-mcp-antigravity-prompt');
  await expect(promptEl).toContainText(`${origin}/mcp`);
  await expect(promptEl).toContainText('Antigravity 플러그인 폴더');

  await expectNoRawKeys(page, '#panel-mcp');
  expect(errors).toEqual([]);
  await context.close();
});
