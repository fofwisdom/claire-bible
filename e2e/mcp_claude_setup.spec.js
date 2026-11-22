const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('English /settings/mcp provides the Claude quick setup prompt, terminal commands, and English guide link', async ({ browser, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const context = await browser.newContext({ locale: 'en-US' });
  await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
  const page = await context.newPage();
  const errors = watchErrors(page);

  await authenticateOwner(page, 'mcpclaude001');
  await page.goto('/settings/mcp', { waitUntil: 'domcontentloaded' });

  const expectedMcpUrl = `${origin}/mcp`;
  const promptEl = page.locator('#pref-mcp-claude-prompt');
  await expect(promptEl).toBeVisible();
  await expect(promptEl).toContainText('claude plugin install claire --marketplace fofwisdom/claire-agents');
  await expect(promptEl).toContainText(`claude mcp add --transport http --scope user claire ${expectedMcpUrl}`);
  await expect(promptEl).toContainText('claude mcp login claire');

  await page.locator('#pref-mcp-copy-claude-prompt').click();
  await expect(page.locator('#pref-toast')).toContainText('Copied to clipboard.');
  const copied = await page.evaluate(() => navigator.clipboard.readText());
  expect(copied).toBe(await promptEl.textContent());

  await expect(page.locator('#pref-mcp-claude-guide')).toHaveAttribute('href', 'https://github.com/fofwisdom/claire-agents/blob/main/docs/claude.en.md');

  await page.locator('#pref-mcp-marketplace-advanced > summary').click();
  const setupEl = page.locator('#pref-mcp-claude-setup');
  await expect(setupEl).toBeVisible();
  expect(await setupEl.textContent()).toBe([
    'claude plugin install claire --marketplace fofwisdom/claire-agents',
    `claude mcp add --transport http --scope user claire "${expectedMcpUrl}"`,
    'claude mcp login claire',
  ].join('\n'));

  await expect(page.locator('#pref-mcp-snippet-cli')).toHaveText(`claude mcp add --transport http --scope user claire "${expectedMcpUrl}"`);

  await expectNoRawKeys(page, '#panel-mcp');
  expect(errors).toEqual([]);
  await context.close();
});

test('Korean /settings/mcp provides the localized Claude prompt and Korean guide link', async ({ browser, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const context = await browser.newContext({ locale: 'ko-KR' });
  const page = await context.newPage();
  const errors = watchErrors(page);

  await authenticateOwner(page, 'mcpclaude002');
  await page.goto('/settings/mcp', { waitUntil: 'domcontentloaded' });

  const promptEl = page.locator('#pref-mcp-claude-prompt');
  await expect(promptEl).toBeVisible();
  await expect(promptEl).toContainText('이 컴퓨터에 Claire Bible을 설정해 줘');
  await expect(promptEl).toContainText(`claude mcp add --transport http --scope user claire ${origin}/mcp`);
  await expect(page.locator('#pref-mcp-claude-guide')).toHaveAttribute('href', 'https://github.com/fofwisdom/claire-agents/blob/main/docs/claude.ko.md');

  await expectNoRawKeys(page, '#panel-mcp');
  expect(errors).toEqual([]);
  await context.close();
});
