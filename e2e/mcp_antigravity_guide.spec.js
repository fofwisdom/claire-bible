const fs = require('node:fs');
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('English /settings/mcp provides quick setup prompt copy, mcp_config.json download, and English GUI guide link', async ({ browser, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const context = await browser.newContext({ locale: 'en-US', acceptDownloads: true });
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

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    page.locator('#pref-mcp-download-mcp-config').click(),
  ]);
  expect(download.suggestedFilename()).toBe('mcp_config.json');
  await expect(page.locator('#pref-toast')).toContainText('Downloaded mcp_config.json.');
  const downloadPath = await download.path();
  expect(downloadPath).toBeTruthy();
  const parsed = JSON.parse(fs.readFileSync(downloadPath, 'utf8'));
  expect(parsed).toEqual({ mcpServers: { claire: { url: expectedMcpUrl } } });

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
  await expect(promptEl).toContainText('~/.gemini/config/plugins/claire에 설치');

  await expectNoRawKeys(page, '#panel-mcp');
  expect(errors).toEqual([]);
  await context.close();
});
