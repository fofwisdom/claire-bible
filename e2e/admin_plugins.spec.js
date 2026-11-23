const fs = require('node:fs');
const path = require('node:path');
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /admin/plugins against the real server (docs/implementation/INGESTION.md §2.2): the server administrator puts a test plugin into the plugin folder of the E2E server (CLAIRE_PLUGINS_DIR of playwright.config.js), switches it on and off, and ingests a payload that it takes. The test plugins are offline: they answer from fixed text.

const PLUGIN_FOLDER = path.resolve(__dirname, '..', 'data', 'e2e-plugins', 'ingest_adapter');
const HOST = 'e2e-plugin.test';
const CORE_COMMIT = 'b8694402b6ee0ce08f5af1a6af2853bb911575d5';

function writePlugin(id, name, title) {
  const folder = path.join(PLUGIN_FOLDER, id);
  fs.mkdirSync(folder, { recursive: true, mode: 0o755 });
  const manifest = [
    'manifest = 1', `id = "${id}"`, 'type = "ingest_adapter"', 'revision = 1', `core_commit = "${CORE_COMMIT}"`, 'api = 1',
    'entry = "adapter:register"', 'license = "MIT"', 'maturity = "experimental"', '',
    '[text.en]', `name = "${name}"`, 'description = "Test plugin of the E2E tests. It answers from fixed text."', '',
    '[[provides]]', 'kind = "fetcher"', `name = "${id.replace('.', '_')}"`, 'priority = 100', `hosts = ["${HOST}"]`, `source_types = ["${id}"]`, '',
    '[permissions]', `network = ["${HOST}"]`, '',
  ].join('\n');
  const adapter = [
    'from claire.plugin_sdk import BaseFetcher, Document, content_hash',
    '',
    `TEXT = "${title}. " + "The plugin gives this fixed text for the E2E test of the plugin switch. " * 12`,
    '',
    '',
    'class Fetcher(BaseFetcher):',
    '    @classmethod',
    '    def can_handle(cls, url):',
    '        return "/item/" in url',
    '',
    '    @classmethod',
    '    def fetch(cls, url, **kwargs):',
    `        return Document(url=url, canonical_url=url, title="${title}", raw_text=TEXT, source_type="${id}", content_hash=content_hash(url, TEXT))`,
    '',
    '',
    'def register(ctx):',
    `    ctx.fetcher(Fetcher, name="${id.replace('.', '_')}", priority=100, hosts=("${HOST}",))`,
    '',
  ].join('\n');
  fs.writeFileSync(path.join(folder, 'plugin.toml'), manifest, { mode: 0o644 });
  fs.writeFileSync(path.join(folder, '__init__.py'), '', { mode: 0o644 });
  fs.writeFileSync(path.join(folder, 'adapter.py'), adapter, { mode: 0o644 });
}

function row(page, id) {
  return page.locator(`tr[data-plugin-id="${id}"]`);
}

async function switchOn(page, id) {
  await row(page, id).locator('input[data-plugin-switch]').click();
  const dialog = page.locator('#admin-plugin-dialog');
  await expect(dialog).toBeVisible();
  // Cancel has the focus; the dialog shows the digest of the files.
  await expect(dialog.locator('button[value="cancel"]')).toBeFocused();
  await expect(dialog).toContainText('sha256:');
  await Promise.all([page.waitForEvent('load'), dialog.locator('#admin-plugin-dialog-confirm').click()]);
}

// The ingest request goes from the page, as the application sends it (same origin, the session cookie).
async function ingest(page, url) {
  return page.evaluate(async (payload) => {
    const response = await fetch('/ingest', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify({ payload, extraction_mode: 'provider' }) });
    let body = null;
    try { body = await response.json(); } catch (e) { body = null; }
    return { status: response.status, body };
  }, url);
}

test('/admin/plugins: the server administrator installs, switches on and off, and selects one plugin of an overlap group', async ({ page }) => {
  test.setTimeout(120_000);
  fs.rmSync(PLUGIN_FOLDER, { recursive: true, force: true });
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'adminplug1');

  // A new plugin of the plugin folder is "new" and does not load.
  writePlugin('example.e2e_alpha', 'E2E Alpha', 'E2E alpha document');
  await page.goto('/admin/plugins', { waitUntil: 'domcontentloaded' });
  await expect(row(page, 'example.e2e_alpha')).toHaveAttribute('data-plugin-state', 'new');
  await expect(row(page, 'claire.youtube')).toHaveAttribute('data-plugin-state', 'enabled');
  await expectNoRawKeys(page);

  // Switch on (recent sign-in of the token login), then a payload of its host goes to the plugin.
  await switchOn(page, 'example.e2e_alpha');
  await expect(row(page, 'example.e2e_alpha')).toHaveAttribute('data-plugin-state', 'enabled');
  const first = await ingest(page, `https://${HOST}/item/1`);
  expect(first.status).toBe(200);
  expect(first.body.error).toBeNull();
  expect(first.body.title).toBe('E2E alpha document');
  expect(first.body.source_type).toBe('example.e2e_alpha');

  // Switch off: the same host now takes the core route (the web chain), which cannot reach a .test host.
  await Promise.all([page.waitForEvent('load'), row(page, 'example.e2e_alpha').locator('input[data-plugin-switch]').click()]);
  await expect(row(page, 'example.e2e_alpha')).toHaveAttribute('data-plugin-state', 'disabled');
  const second = await ingest(page, `https://${HOST}/item/2`);
  expect(second.body && second.body.source_type === 'example.e2e_alpha').toBeFalsy();
  expect(second.status !== 200 || (second.body && second.body.error)).toBeTruthy();

  // Overlap: a second plugin claims the same host. Only one plugin of the group can be on; the dialog names the plugin that goes off.
  await switchOn(page, 'example.e2e_alpha');
  writePlugin('example.e2e_beta', 'E2E Beta', 'E2E beta document');
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(row(page, 'example.e2e_beta')).toHaveAttribute('data-plugin-state', 'new');
  await row(page, 'example.e2e_beta').locator('input[data-plugin-switch]').click();
  await expect(page.locator('#admin-plugin-dialog [data-plugin-replace]')).toContainText('example.e2e_alpha');
  await expect(page.locator('#admin-plugin-dialog-confirm')).toContainText('example.e2e_alpha');
  await Promise.all([page.waitForEvent('load'), page.locator('#admin-plugin-dialog-confirm').click()]);
  await expect(row(page, 'example.e2e_beta')).toHaveAttribute('data-plugin-state', 'enabled');
  await expect(row(page, 'example.e2e_alpha')).toHaveAttribute('data-plugin-state', 'disabled');
  const third = await ingest(page, `https://${HOST}/item/3`);
  expect(third.body.title).toBe('E2E beta document');

  expect(errors.filter((e) => !/Failed to load resource/.test(e))).toEqual([]);
  fs.rmSync(PLUGIN_FOLDER, { recursive: true, force: true });
});
