const { test, expect } = require('@playwright/test');
const { authenticateOwner, json, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /admin/audit: the audit records of the server in the tab set of /admin/federation. The server renders the administrator actions (admin_audit); the MCP calls come from GET /api/admin/mcp/telemetry in the shape of query_mcp_telemetry (store/telemetry.py).

function tab(page, id) {
  return page.locator('#admin-audit-tabs [role="tab"][data-tab="' + id + '"]');
}

function panel(page, id) {
  return page.locator('#admin-audit-tabs [role="tabpanel"][data-tabpanel="' + id + '"]');
}

test('/admin/audit: the administrator actions and the MCP calls are two tabs; /admin/mcp has no call records', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'auditadm1');
  const telemetryQueries = [];
  await page.route((u) => u.pathname === '/api/admin/mcp/telemetry', async (route) => {
    const url = new URL(route.request().url());
    telemetryQueries.push(url.search);
    return json(route, { records: [{ ts: '2026-11-23T10:00:00+00:00', call_type: 'tool_call', tool_name: 'search_knowledge', status: 'success', duration_ms: 12.5, response_bytes: 512, client_id: 'claude-code' }], count: 1 });
  });

  await page.goto('/admin/audit', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#console-sidebar a[href="/admin/audit"]')).toHaveAttribute('aria-current', 'page');

  // Two tabs. The administrator actions show first; one panel shows at a time.
  const tabs = page.locator('#admin-audit-tabs [role="tab"]');
  await expect(tabs).toHaveCount(2);
  await expect(tabs).toHaveText([/관리자 작업/, /MCP 호출/]);
  await expect(panel(page, 'admin')).toBeVisible();
  await expect(page.locator('#admin-audit-actions-card')).toBeVisible();
  await expect(panel(page, 'mcp')).toBeHidden();
  await expectNoRawKeys(page);

  // The MCP tab shows the call records from the API.
  await tab(page, 'mcp').click();
  await expect(tab(page, 'mcp')).toHaveAttribute('aria-selected', 'true');
  await expect(panel(page, 'admin')).toBeHidden();
  const rows = page.locator('#pref-mcp-audit-tbody tr');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('search_knowledge');
  await expect(rows.first()).toContainText('claude-code');
  expect(telemetryQueries[0]).toContain('limit=50');

  // A filter change reads the records again with the filter.
  await page.locator('#admin-mcp-filter-status').selectOption('ERROR');
  await expect.poll(() => telemetryQueries.length).toBe(2);
  expect(telemetryQueries[1]).toContain('status=ERROR');

  // Back to the first tab.
  await tab(page, 'admin').click();
  await expect(tab(page, 'admin')).toHaveAttribute('aria-selected', 'true');
  await expect(panel(page, 'admin')).toBeVisible();

  // /admin/mcp keeps the RBAC comparison and the support bundle, and has no call records.
  await page.locator('#console-sidebar a[href="/admin/mcp"]').click();
  await expect(page).toHaveURL(/\/admin\/mcp$/);
  await expect(page.locator('#admin-mcp-rbac-card')).toBeVisible();
  await expect(page.locator('#admin-support-bundle-card')).toBeVisible();
  await expect(page.locator('#admin-mcp-telemetry-card')).toHaveCount(0);

  expect(errors).toEqual([]);
});
