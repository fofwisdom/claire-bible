// The account area (/account/*) against the real E2E server (ADMIN_AND_SETTINGS_PATH_SEPARATION_DESIGN.md decision D6): the old /settings/* paths of the account pages, the profile and security pages, the provider and MCP pages without an account extraction mode (AED4-rev), the old directory path, and the administrator WebMCP page.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('old settings paths open the account pages, and the provider and MCP pages have no account extraction mode', async ({ page, request }) => {
  const errors = watchErrors(page);

  // The old path of the public account directory redirects with 308 and keeps the query.
  const oldDirectory = await request.get('/accounts?page=true', { maxRedirects: 0, headers: { Accept: 'application/json' } });
  expect(oldDirectory.status()).toBe(308);
  expect(oldDirectory.headers()['location']).toBe('/directory?page=true');

  await authenticateOwner(page, 'acctarea01');

  // The old key page opens the provider page of the account area.
  await page.goto('/settings/keys', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/account\/providers$/);
  await expect(page.locator('body')).toHaveAttribute('data-active-area', 'account');
  await expect(page.locator('#console-sidebar a[href="/account/providers"]')).toHaveAttribute('aria-current', 'page');

  // The sidebar has the two account groups before the groups of /settings, and the AI group has the four AI pages (the administrator has the tokens in /admin).
  const groups = await page.locator('#console-sidebar .console-nav-group').evaluateAll((els) => els.map((el) => el.getAttribute('data-nav-group')));
  expect(groups.slice(0, 4)).toEqual(['workspace', 'ai', 'kb', 'syndication']);
  const aiLinks = await page.locator('#console-grp-ai a').evaluateAll((els) => els.map((el) => new URL(el.href).pathname));
  expect(aiLinks).toEqual(['/account/providers', '/account/mcp', '/account/webmcp']);

  // Each ingest selects its extraction mode in the ingest dialog (AED4-rev): the provider page has no mode control, and the MCP guide has two steps and no mode row.
  await expect(page.locator('#settings-extraction-mode')).toHaveCount(0);
  await expect(page.locator('#pref-account-extraction-mode')).toHaveCount(0);
  await page.goto('/account/mcp', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#pref-mcp-agent-guide')).toBeVisible();
  await expect(page.locator('#pref-mcp-extraction-mode')).toHaveCount(0);
  await expect(page.locator('#pref-mcp-agent-guide ol.pref-flow > li')).toHaveCount(2);

  // The old account page opens the profile page. The password form is on the security page only.
  await page.goto('/settings/account', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/account\/profile$/);
  await expect(page.locator('#settings-account-info')).toBeVisible();
  await expect(page.locator('#settings-password')).toHaveCount(0);
  await expect(page.locator('#settings-extraction-mode')).toHaveCount(0);
  await page.locator('#console-sidebar a[href="/account/security"]').click();
  await expect(page).toHaveURL(/\/account\/security$/);
  await expect(page.locator('#settings-password')).toBeVisible();
  await expect(page.locator('#settings-identities')).toBeVisible();
  await expect(page.locator('#settings-account-info')).toHaveCount(0);

  expect(errors).toEqual([]);
});

test('the server policy of WebMCP is on its own administrator page', async ({ page }) => {
  const errors = watchErrors(page);
  await authenticateOwner(page, 'acctarea02');

  // /admin/mcp has the RBAC comparison and no WebMCP policy.
  await page.goto('/admin/mcp', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#admin-mcp-rbac-card')).toBeVisible();
  await expect(page.locator('#admin-webmcp-policy-card')).toHaveCount(0);

  // /admin/webmcp has only the server policy switch. The browser keeps its state.
  await page.locator('#console-sidebar a[href="/admin/webmcp"]').click();
  await expect(page).toHaveURL(/\/admin\/webmcp$/);
  await expect(page.locator('#console-section-body .console-subcard')).toHaveCount(1);
  const toggle = page.locator('#admin-webmcp-enabled');
  const hint = page.locator('#admin-webmcp-policy-hint');
  await expect(toggle).toBeChecked();
  const onText = await hint.textContent();
  await toggle.uncheck();
  await expect(hint).not.toHaveText(onText);
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('#admin-webmcp-enabled')).not.toBeChecked();
  await page.locator('#admin-webmcp-enabled').check();
  await expect(page.locator('#admin-webmcp-policy-hint')).toHaveText(onText);

  // The account page of WebMCP has the settings of the account and no server policy.
  await page.goto('/account/webmcp', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#panel-webmcp')).toBeVisible();
  await expect(page.locator('#pref-webmcp-enabled')).toBeVisible();
  await expect(page.locator('#admin-webmcp-enabled')).toHaveCount(0);

  expect(errors).toEqual([]);
});
