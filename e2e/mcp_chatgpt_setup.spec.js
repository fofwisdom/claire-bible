const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('English /account/mcp provides the ChatGPT (Codex) quick setup prompt, terminal commands, and English guide link', async ({ browser, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const context = await browser.newContext({ locale: 'en-US' });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
  const page = await context.newPage();
  const errors = watchErrors(page);

  await authenticateOwner(page, 'mcpchatgpt001');
  await page.goto('/account/mcp', { waitUntil: 'domcontentloaded' });

  const expectedMcpUrl = `${origin}/mcp`;
  const promptEl = page.locator('#pref-mcp-chatgpt-prompt');
  await expect(promptEl).toBeVisible();
  await expect(promptEl).toContainText('https://github.com/fofwisdom/claire-agents');
  await expect(promptEl).toContainText(`codex mcp add claire --url "${expectedMcpUrl}"`);
  await expect(promptEl).toContainText('codex mcp login claire');

  await page.locator('#pref-mcp-copy-chatgpt-prompt').click();
  await expect(page.locator('#pref-toast')).toContainText('Copied to clipboard.');
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe(await promptEl.textContent());

  await expect(page.locator('#pref-mcp-chatgpt-guide')).toHaveAttribute('href', 'https://github.com/fofwisdom/claire-agents/blob/main/docs/chatgpt.en.md');

  await page.locator('#pref-mcp-marketplace-advanced > summary').click();
  const setupEl = page.locator('#pref-mcp-chatgpt-setup');
  await expect(setupEl).toBeVisible();
  expect(await setupEl.textContent()).toBe([
    `codex mcp add claire --url "${expectedMcpUrl}"`,
    'codex mcp login claire',
  ].join('\n'));

  await expectNoRawKeys(page, '#panel-mcp');
  expect(errors).toEqual([]);
  await context.close();
});

test('Korean /account/mcp provides the localized ChatGPT (Codex) prompt and Korean guide link', async ({ browser, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const context = await browser.newContext({ locale: 'ko-KR' });
  const page = await context.newPage();
  const errors = watchErrors(page);

  await authenticateOwner(page, 'mcpchatgpt002');
  await page.goto('/account/mcp', { waitUntil: 'domcontentloaded' });

  const promptEl = page.locator('#pref-mcp-chatgpt-prompt');
  await expect(promptEl).toBeVisible();
  await expect(promptEl).toContainText('이 컴퓨터의 Codex에 Claire Bible을 설정해 줘');
  await expect(promptEl).toContainText(`codex mcp add claire --url "${origin}/mcp"`);
  await expect(page.locator('#pref-mcp-chatgpt-guide')).toHaveAttribute('href', 'https://github.com/fofwisdom/claire-agents/blob/main/docs/chatgpt.ko.md');

  await expectNoRawKeys(page, '#panel-mcp');
  expect(errors).toEqual([]);
  await context.close();
});
