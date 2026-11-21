const { execFileSync, spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { test, expect } = require('@playwright/test');
const { watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

const REPO_ROOT = path.resolve(__dirname, '..');
const BOOTSTRAP_TOKEN = 'e2e-owner-token-0123456789abcdef';
const ADMIN_PASSWORD = 'ClaireMgrPass!2026';
const MEMBER_PASSWORD = 'E2eMemberPass!2026';
const CODE_PATTERN = /^[0-9A-HJKMNP-TV-Z]{8}$/;

let serverProc = null;
let tmpDir = null;
let isolatedBaseUrl = null;
let isolatedOrigin = null;
let adminStorageState = null;
const serverLogs = [];

function resolvePythonExecutable() {
  if (process.env.CLAIRE_E2E_PYTHON) return process.env.CLAIRE_E2E_PYTHON;
  const localVenv = path.join(REPO_ROOT, '.venv', 'bin', 'python');
  if (fs.existsSync(localVenv)) return localVenv;
  try {
    const commonDir = execFileSync('git', ['-C', REPO_ROOT, 'rev-parse', '--git-common-dir'], { encoding: 'utf8' }).trim();
    if (commonDir) {
      const mainRoot = path.resolve(REPO_ROOT, commonDir, '..');
      const mainVenv = path.join(mainRoot, '.venv', 'bin', 'python');
      if (fs.existsSync(mainVenv)) return mainVenv;
    }
  } catch (_err) {
    // Ignore git discovery errors and fall back to system Python.
  }
  for (const candidate of ['python3', 'python']) {
    try {
      execFileSync(candidate, ['--version'], { stdio: 'ignore' });
      return candidate;
    } catch (_err) {
      // Try next Python binary name.
    }
  }
  throw new Error('No Python executable found for isolated E2E server.');
}

function allocateFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      srv.close((err) => (err ? reject(err) : resolve(port)));
    });
  });
}

async function waitForHealth(url, proc, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (proc.exitCode !== null) throw new Error(`Isolated serve-api exited early (${proc.exitCode}):\n${serverLogs.join('')}`);
    try {
      const res = await fetch(url);
      if (res.status === 200) return;
    } catch (_err) {
      // Server is still binding.
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`Timed out waiting for ${url}:\n${serverLogs.join('')}`);
}

async function stopIsolatedServer() {
  if (!serverProc) return;
  const proc = serverProc;
  serverProc = null;
  if (proc.exitCode === null) {
    proc.kill('SIGTERM');
    await new Promise((resolve) => {
      const timer = setTimeout(() => {
        if (proc.exitCode === null) proc.kill('SIGKILL');
        resolve();
      }, 5000);
      proc.once('exit', () => {
        clearTimeout(timer);
        resolve();
      });
    });
  }
}

async function newIsolatedContext(browser, opts) {
  const o = opts || {};
  const contextOpts = { baseURL: isolatedBaseUrl, serviceWorkers: 'block', locale: o.locale || 'ko-KR' };
  if (o.storageState) contextOpts.storageState = o.storageState;
  if (o.viewport) contextOpts.viewport = o.viewport;
  const context = await browser.newContext(contextOpts);
  if (o.clipboard) await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: isolatedOrigin });
  return context;
}

async function reauthIfNeeded(page, password) {
  const adminModal = page.locator('#console-reauth-modal:not([hidden])');
  const stepUpDlg = page.locator('dialog.claire-dlg[open]');
  const modeReauth = page.locator('form[data-mode-switch-reauth]:not([hidden])');
  const tierReauth = page.locator('form[data-server-tier-reauth]:not([hidden])');
  let kind = null;
  await expect.poll(async () => {
    if (await adminModal.isVisible().catch(() => false)) { kind = 'adminModal'; return true; }
    if (await stepUpDlg.isVisible().catch(() => false)) { kind = 'stepUpDlg'; return true; }
    if (await modeReauth.isVisible().catch(() => false)) { kind = 'modeReauth'; return true; }
    if (await tierReauth.isVisible().catch(() => false)) { kind = 'tierReauth'; return true; }
    return false;
  }, { timeout: 5000 }).toBe(true);
  if (kind === 'adminModal') {
    await adminModal.locator('#console-reauth-password, input[name="password"]').first().fill(password);
    await adminModal.locator('button[type="submit"]').click();
    await expect(adminModal).toBeHidden();
    return true;
  }
  if (kind === 'stepUpDlg') {
    await stepUpDlg.locator('#claire-stepup-pw, input[name="password"]').first().fill(password);
    await stepUpDlg.locator('button.claire-btn.pri, button[type="submit"]').first().click();
    await expect(stepUpDlg).toHaveCount(0);
    return true;
  }
  if (kind === 'modeReauth') {
    await modeReauth.locator('input[name="password"]').fill(password);
    await modeReauth.locator('button[type="submit"]').click();
    await expect(modeReauth).toBeHidden();
    return true;
  }
  if (kind === 'tierReauth') {
    await tierReauth.locator('input[name="password"]').fill(password);
    await tierReauth.locator('button[type="submit"]').click();
    await expect(tierReauth.locator('[data-server-tier-reauth-result]')).toHaveAttribute('data-state', 'ok');
    return true;
  }
  return false;
}

async function loginViaForm(page, username, password) {
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.locator('form[action="/login"] input#username[name="username"]').fill(username);
  await page.locator('form[action="/login"] input#password[name="password"]').fill(password);
  await Promise.all([page.waitForURL(new RegExp(`/@${username}/?`)), page.locator('form[action="/login"] button[type="submit"]').click()]);
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
}

async function logoutViaMenu(page) {
  await page.locator('#hamburger-btn').click();
  const logoutBtn = page.locator('#menu-logout-form button[type="submit"]');
  await expect(logoutBtn).toBeVisible();
  await Promise.all([page.waitForURL(/\/login/), logoutBtn.click()]);
}

async function readBootstrap(page) {
  return page.evaluate(() => JSON.parse(document.getElementById('console-bootstrap').textContent));
}

function inviteRowByText(page, matcher) {
  return page.locator('#invites-list-section :is(tr, [data-invite-code])').filter({ hasText: matcher }).first();
}

async function extractRowCode(row) {
  const attr = await row.getAttribute('data-invite-code');
  if (attr && CODE_PATTERN.test(attr.trim())) return attr.trim();
  const text = await row.innerText();
  const m = text.match(/\b([0-9A-HJKMNP-TV-Z]{8})\b/);
  expect(m).not.toBeNull();
  return m[1];
}

async function waitForInvitesAfter(page, triggerFn) {
  const listResPromise = page.waitForResponse((r) => r.url().includes('/api/invites') && r.request().method() === 'GET');
  await triggerFn();
  const listRes = await listResPromise;
  expect(listRes.status()).toBe(200);
  const body = await listRes.json().catch(() => null);
  if (body && Array.isArray(body.orderedItems) && body.orderedItems.length > 0) {
    await expect(page.locator('#invites-list-section')).toContainText(body.orderedItems[0].code);
  } else {
    await expect(page.locator('#invites-list-section')).toBeVisible();
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  }
  return body;
}

async function gotoInvitesPage(page) {
  return waitForInvitesAfter(page, () => page.goto('/settings/invites', { waitUntil: 'domcontentloaded' }));
}

async function reloadInvitesPage(page) {
  return waitForInvitesAfter(page, () => page.reload({ waitUntil: 'domcontentloaded' }));
}

async function setAccountLocale(page, localeValue) {
  await page.goto('/settings/account', { waitUntil: 'domcontentloaded' });
  const patchPromise = page.waitForResponse((r) => r.url().endsWith('/api/me') && r.request().method() === 'PATCH');
  const reloadPromise = page.waitForEvent('domcontentloaded');
  await page.locator('#pref-account-locale').selectOption(localeValue);
  expect((await patchPromise).status()).toBe(200);
  await reloadPromise;
  if (localeValue) await expect(page.locator('html')).toHaveAttribute('lang', localeValue);
}

async function assertRowStatus(row, expectedStatus) {
  const patterns = { active: /\bactive\b|활성/i, exhausted: /\bexhausted\b|소진|모두 사용/i, revoked: /\brevoked\b|폐기됨|폐기/i };
  const pat = patterns[expectedStatus];
  if (!pat) throw new Error(`Unsupported status assertion: ${expectedStatus}`);
  const explicit = row.locator('[data-invite-status]');
  if ((await explicit.count()) > 0) {
    await expect(explicit.first()).toContainText(pat);
  } else {
    const badges = row.locator('.badge');
    if ((await badges.count()) > 0) await expect(badges.filter({ hasText: pat }).first()).toBeVisible();
    else await expect(row).toContainText(pat);
  }
  if (expectedStatus === 'revoked' || expectedStatus === 'exhausted') {
    const revokeBtn = row.locator('[data-invite-revoke]');
    if ((await revokeBtn.count()) > 0) await expect(revokeBtn).toBeDisabled();
  }
}

async function assertIssuanceDisabledUI(page) {
  const submitBtn = page.locator('#invites-issue-section form button[type="submit"]');
  const submitCount = await submitBtn.count();
  if (submitCount > 0) {
    const anyEnabled = await submitBtn.evaluateAll((els) => els.some((el) => !el.disabled && el.offsetParent !== null));
    expect(anyEnabled).toBe(false);
  }
  const disabledNotice = page.locator('#invites-issue-section :is(.notice, .pref-notice, .hint), #console-section-body :is(.notice, .pref-notice)');
  const hasNotice = (await disabledNotice.count()) > 0 && (await disabledNotice.first().isVisible().catch(() => false));
  const hasDisabledBtn = submitCount > 0 && (await submitBtn.first().isDisabled());
  expect(hasNotice || hasDisabledBtn).toBe(true);
}

async function issueInviteViaUI(page, opts) {
  const o = opts || {};
  const form = page.locator('#invites-issue-section form');
  await expect(form).toBeVisible();
  await form.locator('input[name="max_uses"]').fill(String(o.maxUses != null ? o.maxUses : 1));
  const reservedInput = form.locator('input[name="reserved_username"]');
  if (await reservedInput.count()) await reservedInput.fill(o.reservedUsername || '');
  const noteInput = form.locator('input[name="note"]');
  if (await noteInput.count()) await noteInput.fill(o.note != null ? o.note : '');
  const resPromise = page.waitForResponse((r) => r.url().includes('/api/invites') && r.request().method() === 'POST');
  await form.locator('button[type="submit"]').click();
  const res = await resPromise;
  expect(res.status()).toBe(201);
  const body = await res.json();
  expect(body.code).toMatch(CODE_PATTERN);
  expect(body.status).toBe('active');
  expect(body.url).toBe(`${isolatedOrigin}/invite/${body.code}`);
  await expect(page.locator('#invites-result')).toBeVisible();
  await expect(page.locator('#invites-result')).toContainText(body.code);
  await expectNoRawKeys(page, '#console-main');
  return body;
}

async function copyInviteLink(page, row, opts) {
  const o = opts || {};
  await page.bringToFront();
  const code = await extractRowCode(row);
  const expectedUrl = `${isolatedOrigin}/invite/${code}`;
  const copyBtn = row.locator('[data-invite-copy]');
  await expect(copyBtn).toBeVisible();
  const prevResult = (await page.locator('#invites-result').innerText().catch(() => '')).trim();
  const prevToastCount = await page.locator('.claire-toast').count();
  await copyBtn.click();
  await expect.poll(async () => {
    const resultText = (await page.locator('#invites-result').innerText().catch(() => '')).trim();
    const toastCount = await page.locator('.claire-toast').count();
    const lastToastText = toastCount > 0 ? (await page.locator('.claire-toast').last().innerText().catch(() => '')).trim() : '';
    const hasNewFeedback = (toastCount > prevToastCount && lastToastText !== '') || (resultText !== prevResult && resultText !== '');
    if (o.noClipboardRead) return hasNewFeedback;
    const clip = await page.evaluate(() => navigator.clipboard.readText()).catch(() => '');
    return hasNewFeedback || clip === expectedUrl;
  }).toBe(true);
  await expectNoRawKeys(page, '#console-main');
  if (o.noClipboardRead) return null;
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText()).catch(() => '')).toBe(expectedUrl);
  return page.evaluate(() => navigator.clipboard.readText());
}

async function revokeInviteInUI(page, row) {
  const revokeBtn = row.locator('[data-invite-revoke]');
  await expect(revokeBtn).toBeVisible();
  await expect(revokeBtn).toBeEnabled();
  const resPromise = page.waitForResponse((r) => r.url().includes('/api/invites') && r.request().method() === 'PATCH');
  await revokeBtn.click();
  const res = await resPromise;
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.revoked).toBe(true);
  expect(body.status).toBe('revoked');
  await expectNoRawKeys(page, '#console-main');
  return body;
}

async function setMemberInvitesEnabled(page, enabled) {
  await page.goto('/admin/server', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#server-invites-policy')).toBeVisible();
  const toggle = page.locator('#admin-member-invites-toggle');
  await expect(toggle).toBeVisible();
  const currentlyChecked = await toggle.isChecked();
  if (currentlyChecked === enabled) return;
  const firstResPromise = page.waitForResponse((r) => r.url().endsWith('/api/admin/server/invites') && r.request().method() === 'POST');
  await toggle.click();
  let res = await firstResPromise;
  if (res.status() === 401) {
    const retryPromise = page.waitForResponse((r) => r.url().endsWith('/api/admin/server/invites') && r.request().method() === 'POST');
    const handled = await reauthIfNeeded(page, ADMIN_PASSWORD);
    expect(handled).toBe(true);
    res = await retryPromise;
  }
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(body.member_invites_enabled).toBe(enabled);
  await page.reload({ waitUntil: 'domcontentloaded' });
  if (enabled) await expect(page.locator('#admin-member-invites-toggle')).toBeChecked();
  else await expect(page.locator('#admin-member-invites-toggle')).not.toBeChecked();
}

async function assertBottomPlacement(page, opts) {
  const o = opts || {};
  const layout = await page.evaluate(() => {
    const body = document.getElementById('console-section-body');
    const issueEl = document.getElementById('invites-issue-section');
    const resultEl = document.getElementById('invites-result');
    const listEl = document.getElementById('invites-list-section');
    const cards = Array.from(document.querySelectorAll('#console-section-body :is(section.card, section.console-subcard, .pref-card)')).filter((el) => el === listEl || !listEl || !listEl.contains(el));
    const lastId = cards.length ? cards[cards.length - 1].id : null;
    const listRect = listEl ? listEl.getBoundingClientRect() : null;
    let allPrecedingAbove = true;
    const offendingBelow = [];
    if (body && listEl && listRect) {
      const candidates = Array.from(body.querySelectorAll('section, article, form, .card, .console-subcard, .pref-card, .notice, .pref-notice, .hint, .result, button, a, input, select, textarea, table, h2, h3, p'));
      for (const el of candidates) {
        if (el === listEl || el.contains(listEl) || listEl.contains(el)) continue;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.position === 'fixed') continue;
        if (el.classList.contains('sr-only')) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && r.bottom > listRect.top + 1) {
          allPrecedingAbove = false;
          offendingBelow.push(`${el.tagName.toLowerCase()}#${el.id || ''}.${el.className || ''}`);
        }
      }
    }
    return { lastId, hasIssue: Boolean(issueEl), hasList: Boolean(listEl), allPrecedingAbove, offendingBelow, resultVisible: Boolean(resultEl && resultEl.getBoundingClientRect().height > 0), clientWidth: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth };
  });
  expect(layout.hasList).toBe(true);
  expect(layout.lastId).toBe('invites-list-section');
  expect(layout.offendingBelow).toEqual([]);
  expect(layout.allPrecedingAbove).toBe(true);
  expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth);
  const listBox = await page.locator('#invites-list-section').boundingBox();
  expect(listBox).not.toBeNull();
  if (o.requireIssueSection) {
    expect(layout.hasIssue).toBe(true);
    const issueBox = await page.locator('#invites-issue-section').boundingBox();
    expect(issueBox).not.toBeNull();
    expect(listBox.y).toBeGreaterThanOrEqual(issueBox.y + issueBox.height - 1);
  }
  if (layout.resultVisible) {
    const resultBox = await page.locator('#invites-result').boundingBox();
    if (resultBox && resultBox.height > 0) expect(listBox.y).toBeGreaterThanOrEqual(resultBox.y + resultBox.height - 1);
  }
}

async function assertMemberIsolation(page) {
  const isolation = await page.evaluate(() => {
    const adminLinks = Array.from(document.querySelectorAll('a[href*="/admin"]')).map((el) => el.getAttribute('href'));
    const adminScripts = Array.from(document.querySelectorAll('script[src*="console_admin"]')).map((el) => el.getAttribute('src'));
    const catalogEl = document.getElementById('claire-i18n-catalog');
    const catalogKeys = catalogEl && catalogEl.textContent ? Object.keys(JSON.parse(catalogEl.textContent)) : [];
    const adminKeys = catalogKeys.filter((k) => k.startsWith('console.js.admin.'));
    const nonJsConsoleKeys = catalogKeys.filter((k) => k.startsWith('console.') && !k.startsWith('console.js.'));
    return { adminLinks, adminScripts, adminKeys, nonJsConsoleKeys, catalogKeys };
  });
  expect(isolation.adminLinks).toEqual([]);
  expect(isolation.adminScripts).toEqual([]);
  expect(isolation.adminKeys).toEqual([]);
  expect(isolation.nonJsConsoleKeys).toEqual([]);
  return isolation.catalogKeys;
}

async function fetchInvitesApi(context) {
  const res = await context.request.get('/api/invites?page=true');
  expect(res.status()).toBe(200);
  const body = await res.json();
  expect(Array.isArray(body.orderedItems)).toBe(true);
  return body;
}

test.describe.serial('Invite code issuance, management, and signup (R1-R5)', () => {
  test.beforeAll(async () => {
    test.setTimeout(90_000);
    const py = resolvePythonExecutable();
    const port = await allocateFreePort();
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'claire-invites-e2e-'));
    const dbPath = path.join(tmpDir, 'invites_isolated.db');
    const pluginsDir = path.join(tmpDir, 'e2e-plugins');
    isolatedBaseUrl = `http://127.0.0.1:${port}/`;
    isolatedOrigin = `http://127.0.0.1:${port}`;
    const env = { ...process.env, PYTHONPATH: process.env.PYTHONPATH ? `src:${process.env.PYTHONPATH}` : 'src', CLAIRE_MULTI_THEME: '1', CLAIRE_DB_PATH: dbPath, CLAIRE_INJECT_PORT: String(port), CLAIRE_PUBLIC_URL: isolatedBaseUrl, CLAIRE_ENVIRONMENT: 'development', CLAIRE_ALLOW_INSECURE_HTTP: 'true', CLAIRE_INJECT_TOKEN: BOOTSTRAP_TOKEN, CLAIRE_SECRET_KEY: crypto.randomBytes(32).toString('hex'), CLAIRE_PLUGINS_DIR: pluginsDir };
    execFileSync(py, [path.join(REPO_ROOT, 'e2e', 'seed_db.py')], { cwd: REPO_ROOT, env, stdio: 'pipe' });
    serverProc = spawn(py, ['-m', 'claire_bible.cli', 'serve-api'], { cwd: REPO_ROOT, env, stdio: ['ignore', 'pipe', 'pipe'] });
    serverProc.stdout.on('data', (chunk) => {
      serverLogs.push(chunk.toString('utf8'));
      if (serverLogs.length > 200) serverLogs.shift();
    });
    serverProc.stderr.on('data', (chunk) => {
      serverLogs.push(chunk.toString('utf8'));
      if (serverLogs.length > 200) serverLogs.shift();
    });
    await waitForHealth(`${isolatedOrigin}/health`, serverProc, 30_000);
  });

  test.afterAll(async () => {
    await stopIsolatedServer();
    if (tmpDir) fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  test('00. Bootstrap: single-mode baseline, admin password, tier, and switch to network mode', async ({ browser }) => {
    test.setTimeout(60_000);
    const context = await newIsolatedContext(browser, { clipboard: true });
    const page = await context.newPage();
    const errors = watchErrors(page);
    try {
      await page.goto(`/?t=${BOOTSTRAP_TOKEN}`, { waitUntil: 'domcontentloaded' });
      await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

      const singlePage = await page.goto('/settings/invites', { waitUntil: 'domcontentloaded' });
      expect(singlePage.status()).toBe(200);
      await expect(page.locator('#invites-issue-section form')).toHaveCount(0);
      await expect(page.locator('#console-section-body :is(.notice, .pref-notice)')).toBeVisible();
      await expectNoRawKeys(page, '#console-main');
      const singleApi = await context.request.get('/api/invites?page=true');
      expect(singleApi.status()).toBe(404);

      await page.goto('/settings/account', { waitUntil: 'domcontentloaded' });
      await page.locator('#settings-password input[name="new_password"]').fill(ADMIN_PASSWORD);
      await page.locator('#settings-password input[name="new_password_confirm"]').fill(ADMIN_PASSWORD);
      const pwResPromise = page.waitForResponse((r) => r.url().endsWith('/api/me/password') && r.request().method() === 'POST');
      await page.locator('#settings-password button[type="submit"]').click();
      expect((await pwResPromise).status()).toBe(200);
      await expect(page.locator('#settings-password [data-settings-result]')).toHaveAttribute('data-state', 'ok');

      await page.goto('/admin/server', { waitUntil: 'domcontentloaded' });
      const tierResPromise = page.waitForResponse((r) => r.url().endsWith('/api/admin/server-tier') && r.request().method() === 'PUT');
      await page.locator('#server-tier form[data-server-tier-form] button[type="submit"]').click();
      let tierRes = await tierResPromise;
      if (tierRes.status() === 401) {
        expect(await reauthIfNeeded(page, ADMIN_PASSWORD)).toBe(true);
        const retryTier = page.waitForResponse((r) => r.url().endsWith('/api/admin/server-tier') && r.request().method() === 'PUT');
        await page.locator('#server-tier form[data-server-tier-form] button[type="submit"]').click();
        tierRes = await retryTier;
      }
      expect(tierRes.status()).toBe(200);
      await expect(page.locator('#server-tier [data-server-tier-result]')).toHaveAttribute('data-state', 'ok');

      await page.reload({ waitUntil: 'domcontentloaded' });
      const modeForm = page.locator('#server-mode form[data-mode-switch-form]');
      await expect(modeForm.locator('button[type="submit"]')).toBeEnabled();
      await modeForm.locator('input[name="confirm"]').check();
      const modeResPromise = page.waitForResponse((r) => r.url().endsWith('/api/admin/server/mode') && r.request().method() === 'POST');
      await modeForm.locator('button[type="submit"]').click();
      let modeRes = await modeResPromise;
      if (modeRes.status() === 401) {
        const retryMode = page.waitForResponse((r) => r.url().endsWith('/api/admin/server/mode') && r.request().method() === 'POST');
        expect(await reauthIfNeeded(page, ADMIN_PASSWORD)).toBe(true);
        modeRes = await retryMode;
      }
      expect(modeRes.status()).toBe(200);
      await expect.poll(async () => {
        const r = await context.request.get('/api/admin/server');
        return r.status() === 200 ? (await r.json()).mode : null;
      }).toBe('network');

      await page.goto('/admin/server', { waitUntil: 'domcontentloaded' });
      await expect(page.locator('#server-invites-policy')).toBeVisible();
      await expect(page.locator('#admin-member-invites-toggle')).not.toBeChecked();
      const srvRes = await context.request.get('/api/admin/server');
      expect(srvRes.status()).toBe(200);
      const srvJson = await srvRes.json();
      expect(srvJson.mode).toBe('network');
      expect(srvJson.member_invites_enabled).toBe(false);
      await expectNoRawKeys(page, '#console-main');

      adminStorageState = await context.storageState();
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  });

  test('T1.1: Admin issuance, immediate status display (F1/R1) & bottom placement layout (F4/R4)', async ({ browser }) => {
    const context = await newIsolatedContext(browser, { storageState: adminStorageState, clipboard: true });
    const page = await context.newPage();
    const errors = watchErrors(page);
    try {
      await page.setViewportSize({ width: 1280, height: 800 });
      await gotoInvitesPage(page);
      await expectNoRawKeys(page, '#console-main');
      const created = await issueInviteViaUI(page, { maxUses: 1, note: 't1-immediate' });
      const row = inviteRowByText(page, 't1-immediate');
      await expect(row).toBeVisible();
      await expect(row).toContainText(created.code);
      await expect(row).toContainText('0 / 1');
      await assertRowStatus(row, 'active');
      await expect(row.locator('[data-invite-copy]')).toBeVisible();
      await expect(row.locator('[data-invite-revoke]')).toBeVisible();
      await expect(row.locator('[data-invite-revoke]')).toBeEnabled();

      await assertBottomPlacement(page, { requireIssueSection: true });

      const second = await issueInviteViaUI(page, { maxUses: 2, note: 't1-second' });
      const secondRow = inviteRowByText(page, 't1-second');
      await expect(secondRow).toContainText(second.code);
      await assertRowStatus(secondRow, 'active');
      await assertBottomPlacement(page, { requireIssueSection: true });

      await reloadInvitesPage(page);
      const persistedRow = inviteRowByText(page, 't1-immediate');
      await expect(persistedRow).toContainText(created.code);
      await expect(persistedRow).toContainText('0 / 1');
      await assertRowStatus(persistedRow, 'active');
      await expect(persistedRow.locator('[data-invite-revoke]')).toBeEnabled();
      await expectNoRawKeys(page, '#console-main');
      const apiPage = await fetchInvitesApi(context);
      const apiItem = apiPage.orderedItems.find((it) => it.code === created.code);
      expect(apiItem).toBeDefined();
      expect(apiItem.status).toBe('active');
      expect(apiItem.url).toBe(`${isolatedOrigin}/invite/${created.code}`);

      await page.goto('/admin/accounts', { waitUntil: 'domcontentloaded' });
      const adminAccountsCheck = await page.evaluate(() => {
        const sec = document.getElementById('admin-invites-section');
        if (!sec) return { present: false, isLast: true };
        const cards = Array.from(document.querySelectorAll('#console-section-body section.card'));
        return { present: true, isLast: cards.indexOf(sec) === cards.length - 1 };
      });
      if (adminAccountsCheck.present) expect(adminAccountsCheck.isLast).toBe(true);
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  });

  test('T1.2: Per-code copy signup link & end-to-end member signup (F2/R2)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState, clipboard: true });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    let anonCtx = null;
    try {
      await gotoInvitesPage(adminPage);
      const row = inviteRowByText(adminPage, 't1-immediate');
      await expect(row).toBeVisible();
      const code = await extractRowCode(row);

      const copiedUrl = await copyInviteLink(adminPage, row);
      expect(copiedUrl).toBe(`${isolatedOrigin}/invite/${code}`);

      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const anonPage = await anonCtx.newPage();
      const anonErrors = watchErrors(anonPage);
      const getRes = await anonPage.goto(copiedUrl, { waitUntil: 'domcontentloaded' });
      expect(getRes.status()).toBe(200);
      await expect(anonPage.locator(`form[action="/invite/${code}"]`)).toBeVisible();
      await expect(anonPage.locator('body')).toContainText(code);
      await anonPage.locator('#username').fill('e2e_member_a');
      await anonPage.locator('#display_name').fill('Member Alpha');
      await anonPage.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([anonPage.waitForURL(/\/@e2e_member_a\/?/), anonPage.locator(`form[action="/invite/${code}"] button[type="submit"]`).click()]);
      await expect.poll(() => anonPage.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
      expect(anonErrors).toEqual([]);

      await reloadInvitesPage(adminPage);
      const updatedRow = inviteRowByText(adminPage, 't1-immediate');
      await expect(updatedRow).toContainText('1 / 1');
      await assertRowStatus(updatedRow, 'exhausted');
      await expectNoRawKeys(adminPage, '#console-main');
      const apiPage = await fetchInvitesApi(adminCtx);
      const apiItem = apiPage.orderedItems.find((it) => it.code === code);
      expect(apiItem.status).toBe('exhausted');
      expect(apiItem.uses_count).toBe(1);
      expect(adminErrors).toEqual([]);
    } finally {
      if (anonCtx) await anonCtx.close();
      await adminCtx.close();
    }
  });

  test('T1.3: Member access, scoped isolation & admin server toggle (F5/R5, F6/R5)', async ({ browser }) => {
    const memberCtx = await newIsolatedContext(browser, { clipboard: true });
    const memberPage = await memberCtx.newPage();
    const memberErrors = watchErrors(memberPage);
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    try {
      await loginViaForm(memberPage, 'e2e_member_a', MEMBER_PASSWORD);
      await memberPage.goto('/settings/account', { waitUntil: 'domcontentloaded' });
      const navLink = memberPage.locator('a[data-nav-section="invites"][href="/settings/invites"]').first();
      await expect(navLink).toBeVisible();
      await waitForInvitesAfter(memberPage, () => navLink.click());
      await expect(memberPage).toHaveURL(/\/settings\/invites/);
      await expectNoRawKeys(memberPage, '#console-main');
      await assertMemberIsolation(memberPage);

      const bootOff = await readBootstrap(memberPage);
      expect(bootOff.is_admin).toBe(false);
      expect(bootOff.member_invites_enabled).toBe(false);
      expect(bootOff.can_issue_invites).toBe(false);
      await expect(memberPage.locator('#invites-list-section')).not.toContainText('t1-immediate');
      await expect(memberPage.locator('#invites-list-section')).not.toContainText('t1-second');
      await assertIssuanceDisabledUI(memberPage);

      const memberApiPostOff = await memberCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1, note: 'blocked-when-off' } });
      expect(memberApiPostOff.status()).toBe(403);
      expect((await memberApiPostOff.json()).error).toBe('invite_issuance_disabled');

      expect((await memberCtx.request.get('/admin/server')).status()).toBe(404);
      expect((await memberCtx.request.get('/api/admin/server')).status()).toBe(404);
      expect((await memberCtx.request.post('/api/admin/server/invites', { headers: { Origin: isolatedOrigin }, data: { member_invites_enabled: true } })).status()).toBe(404);

      await setMemberInvitesEnabled(adminPage, true);

      await reloadInvitesPage(memberPage);
      const bootOn = await readBootstrap(memberPage);
      expect(bootOn.member_invites_enabled).toBe(true);
      expect(bootOn.can_issue_invites).toBe(true);
      const memberCreated = await issueInviteViaUI(memberPage, { maxUses: 3, note: 't1-member-a-multi' });
      const memberRow = inviteRowByText(memberPage, 't1-member-a-multi');
      await expect(memberRow).toContainText(memberCreated.code);
      await expect(memberRow).toContainText('0 / 3');
      await assertRowStatus(memberRow, 'active');
      await expect(memberPage.locator('#invites-list-section')).not.toContainText('t1-immediate');

      await gotoInvitesPage(adminPage);
      await expect(inviteRowByText(adminPage, 't1-immediate')).toBeVisible();
      await expect(inviteRowByText(adminPage, 't1-member-a-multi')).toContainText(memberCreated.code);
      const adminList = await fetchInvitesApi(adminCtx);
      const memberList = await fetchInvitesApi(memberCtx);
      expect(adminList.orderedItems.some((it) => it.note === 't1-immediate')).toBe(true);
      expect(adminList.orderedItems.some((it) => it.note === 't1-member-a-multi')).toBe(true);
      expect(memberList.orderedItems.some((it) => it.note === 't1-immediate')).toBe(false);
      expect(memberList.orderedItems.some((it) => it.note === 't1-member-a-multi')).toBe(true);
      expect(memberErrors).toEqual([]);
      expect(adminErrors).toEqual([]);
    } finally {
      await memberCtx.close();
      await adminCtx.close();
    }
  });

  test('T1.4: Per-code revocation with remaining uses & signup rejection (F3/R3)', async ({ browser }) => {
    const memberCtx = await newIsolatedContext(browser, { clipboard: true });
    const memberPage = await memberCtx.newPage();
    const memberErrors = watchErrors(memberPage);
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    let signupCtx = null;
    let probeCtx = null;
    try {
      await loginViaForm(memberPage, 'e2e_member_a', MEMBER_PASSWORD);
      await gotoInvitesPage(memberPage);
      const multiRow = inviteRowByText(memberPage, 't1-member-a-multi');
      const multiUrl = await copyInviteLink(memberPage, multiRow);
      const multiCode = multiUrl.split('/invite/')[1];

      signupCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const signupPage = await signupCtx.newPage();
      await signupPage.goto(multiUrl, { waitUntil: 'domcontentloaded' });
      await signupPage.locator('#username').fill('e2e_member_b');
      await signupPage.locator('#display_name').fill('Member Beta');
      await signupPage.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([signupPage.waitForURL(/\/@e2e_member_b\/?/), signupPage.locator(`form[action="/invite/${multiCode}"] button[type="submit"]`).click()]);
      await signupCtx.close();
      signupCtx = null;

      await reloadInvitesPage(memberPage);
      const usedRow = inviteRowByText(memberPage, 't1-member-a-multi');
      await expect(usedRow).toContainText('1 / 3');
      await revokeInviteInUI(memberPage, usedRow);
      const revokedRowImmediate = inviteRowByText(memberPage, 't1-member-a-multi');
      await assertRowStatus(revokedRowImmediate, 'revoked');
      await expect.poll(async () => {
        const count = await inviteRowByText(memberPage, 't1-member-a-multi').locator('[data-invite-revoke]').count();
        return count === 0 ? true : await inviteRowByText(memberPage, 't1-member-a-multi').locator('[data-invite-revoke]').isDisabled();
      }).toBe(true);

      await reloadInvitesPage(memberPage);
      await assertRowStatus(inviteRowByText(memberPage, 't1-member-a-multi'), 'revoked');
      const memberApi = await fetchInvitesApi(memberCtx);
      const revokedItem = memberApi.orderedItems.find((it) => it.code === multiCode);
      expect(revokedItem.status).toBe('revoked');
      expect(revokedItem.uses_count).toBe(1);

      probeCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const probePage = await probeCtx.newPage();
      const getRevoked = await probePage.goto(multiUrl, { waitUntil: 'domcontentloaded' });
      expect(getRevoked.status()).toBe(410);
      await expect(probePage.locator('form[action^="/invite/"]')).toHaveCount(0);
      await expect(probePage.locator('h1')).toBeVisible();
      await expect(probePage.locator('h1')).not.toHaveText('');
      await expect(probePage.locator('p').first()).toBeVisible();
      await expect(probePage.locator('p').first()).not.toHaveText('');
      await expect(probePage.locator('a[href="/"]')).toBeVisible();
      await expectNoRawKeys(probePage, 'body');

      const postFormRevoked = await probeCtx.request.post(`/invite/${multiCode}`, { headers: { Origin: isolatedOrigin, Accept: 'text/html' }, form: { username: 'e2e_rejected_1', display_name: 'Rejected One', password: MEMBER_PASSWORD } });
      expect(postFormRevoked.status()).toBe(410);
      expect(postFormRevoked.headers()['content-type']).toContain('text/html');
      const postFormRevokedHtml = await postFormRevoked.text();
      expect(postFormRevokedHtml.trim().startsWith('{')).toBe(false);
      expect(postFormRevokedHtml).toContain('href="/"');
      expect(postFormRevokedHtml).not.toMatch(/\bauth\.invite\.[a-z_.]+\b/);

      const postJsonRevoked = await probeCtx.request.post(`/invite/${multiCode}`, { headers: { Origin: isolatedOrigin, Accept: 'application/json' }, data: { username: 'e2e_rejected_2', display_name: 'Rejected Two', password: MEMBER_PASSWORD } });
      expect(postJsonRevoked.status()).toBe(410);
      expect((await postJsonRevoked.json()).error).toBe('invitation_revoked');

      const adminTarget = await issueInviteViaUI(memberPage, { maxUses: 2, note: 't1-member-a-for-admin-revoke' });
      await gotoInvitesPage(adminPage);
      const adminTargetRow = inviteRowByText(adminPage, 't1-member-a-for-admin-revoke');
      await expect(adminTargetRow).toContainText('0 / 2');
      await revokeInviteInUI(adminPage, adminTargetRow);
      await assertRowStatus(inviteRowByText(adminPage, 't1-member-a-for-admin-revoke'), 'revoked');
      const getAdminRevoked = await probePage.goto(`${isolatedOrigin}/invite/${adminTarget.code}`, { waitUntil: 'domcontentloaded' });
      expect(getAdminRevoked.status()).toBe(410);

      expect(memberErrors).toEqual([]);
      expect(adminErrors).toEqual([]);
    } finally {
      if (signupCtx) await signupCtx.close();
      if (probeCtx) await probeCtx.close();
      await memberCtx.close();
      await adminCtx.close();
    }
  });

  test('T2.1: Issuance parameter bounds, empty note, reserved username & invalid codes (F1, F2, F3)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState, clipboard: true });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    let anonCtx = null;
    try {
      await gotoInvitesPage(adminPage);
      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const anonPage = await anonCtx.newPage();

      // 1. max_uses=1 exhaustion boundary -> second GET/POST returns 410 (never 500).
      const exhaustOne = await issueInviteViaUI(adminPage, { maxUses: 1, note: 't2-exhaust-one' });
      await anonPage.goto(exhaustOne.url, { waitUntil: 'domcontentloaded' });
      await anonPage.locator('#username').fill('e2e_t2_exhausted');
      await anonPage.locator('#display_name').fill('Exhausted User');
      await anonPage.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([anonPage.waitForURL(/\/@e2e_t2_exhausted\/?/), anonPage.locator(`form[action="/invite/${exhaustOne.code}"] button[type="submit"]`).click()]);
      await anonCtx.close();
      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const anonPage2 = await anonCtx.newPage();
      const secondGet = await anonPage2.goto(exhaustOne.url, { waitUntil: 'domcontentloaded' });
      expect(secondGet.status()).toBe(410);
      await expect(anonPage2.locator('h1')).not.toHaveText('');
      await expectNoRawKeys(anonPage2, 'body');
      const secondPost = await anonCtx.request.post(`/invite/${exhaustOne.code}`, { headers: { Origin: isolatedOrigin, Accept: 'text/html' }, form: { username: 'e2e_t2_exhaust_2', display_name: 'Second', password: MEMBER_PASSWORD } });
      expect(secondPost.status()).toBe(410);

      // 2. max_uses=5 upper bound & out-of-range (0, 6).
      const fiveUses = await issueInviteViaUI(adminPage, { maxUses: 5, note: 't2-max-five' });
      await expect(inviteRowByText(adminPage, 't2-max-five')).toContainText('0 / 5');
      expect(fiveUses.max_uses).toBe(5);
      for (const badMax of [0, 6]) {
        const badRes = await adminCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: badMax, note: `t2-bad-max-${badMax}` } });
        expect(badRes.status()).toBe(400);
      }

      // 3. Blank/null/omitted note coercion -> never renders "None", "null", or "undefined".
      const blankUi = await issueInviteViaUI(adminPage, { maxUses: 1, note: '' });
      const blankUiRow = inviteRowByText(adminPage, blankUi.code);
      await expect(blankUiRow).not.toContainText(/\b(None|null|undefined)\b/);
      const nullApi = await adminCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1, note: null } });
      expect(nullApi.status()).toBe(201);
      const nullApiJson = await nullApi.json();
      expect(nullApiJson.note).toBe('');
      await reloadInvitesPage(adminPage);
      await expect(inviteRowByText(adminPage, nullApiJson.code)).not.toContainText(/\b(None|null|undefined)\b/);

      // 4. reserved_username match, mismatch & max_uses > 1 rejection.
      const reservedBadMax = await adminCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 2, reserved_username: 'e2e_reserved_bad' } });
      expect(reservedBadMax.status()).toBe(400);
      const reservedInv = await issueInviteViaUI(adminPage, { maxUses: 1, reservedUsername: 'e2e_reserved_ok', note: 't2-reserved' });
      await anonPage2.goto(reservedInv.url, { waitUntil: 'domcontentloaded' });
      await expect(anonPage2.locator('#username')).toHaveValue('e2e_reserved_ok');
      await expect(anonPage2.locator('#username')).toHaveAttribute('readonly', '');
      const mismatchPost = await anonCtx.request.post(`/invite/${reservedInv.code}`, { headers: { Origin: isolatedOrigin, Accept: 'text/html' }, form: { username: 'e2e_wrong_acct', display_name: 'Wrong Account', password: MEMBER_PASSWORD } });
      expect(mismatchPost.status()).toBe(400);
      expect(mismatchPost.headers()['content-type']).toContain('text/html');
      const mismatchHtml = await mismatchPost.text();
      expect(mismatchHtml.trim().startsWith('{')).toBe(false);
      expect(mismatchHtml).toContain('class="errors"');
      expect(mismatchHtml).toContain('e2e_reserved_ok');
      await anonPage2.locator('#display_name').fill('Reserved User');
      await anonPage2.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([anonPage2.waitForURL(/\/@e2e_reserved_ok\/?/), anonPage2.locator(`form[action="/invite/${reservedInv.code}"] button[type="submit"]`).click()]);

      // 5. Exactly 2 invalid invite probes (well below 10/min rate limit).
      await anonCtx.close();
      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      expect((await anonCtx.request.get(`/invite/${'Z'.repeat(8)}`)).status()).toBe(404);
      expect((await anonCtx.request.get('/invite/BAD!CODE')).status()).toBe(404);

      // 6. expires_in lower and upper bounds (1800 valid lower bound, 1799 & 2592001 rejected).
      const minExpRes = await adminCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1, expires_in: 1800, note: 't2-min-exp' } });
      expect(minExpRes.status()).toBe(201);
      expect((await minExpRes.json()).status).toBe('active');
      expect((await adminCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1, expires_in: 1799 } })).status()).toBe(400);
      expect((await adminCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1, expires_in: 2592001 } })).status()).toBe(400);

      // 7. Browser HTML form validation error rendering preserves remaining uses.
      const htmlErrInv = await issueInviteViaUI(adminPage, { maxUses: 2, note: 't2-html-err' });
      const errPage = await anonCtx.newPage();
      await errPage.goto(htmlErrInv.url, { waitUntil: 'domcontentloaded' });
      await errPage.locator('#username').fill('e2e_member_a');
      await errPage.locator('#display_name').fill('Duplicate User');
      await errPage.locator('#password').fill(MEMBER_PASSWORD);
      const takenRespPromise = errPage.waitForResponse((r) => r.url().includes(`/invite/${htmlErrInv.code}`) && r.request().method() === 'POST');
      await errPage.locator(`form[action="/invite/${htmlErrInv.code}"] button[type="submit"]`).click();
      const takenResp = await takenRespPromise;
      expect(takenResp.status()).toBe(400);
      expect(takenResp.headers()['content-type']).toContain('text/html');
      const takenHtml = await takenResp.text();
      expect(takenHtml.trim().startsWith('{')).toBe(false);
      await expect(errPage.locator('.errors')).toBeVisible();
      await expect(errPage.locator('.errors')).not.toHaveText('');
      await expect(errPage.locator(`form[action="/invite/${htmlErrInv.code}"]`)).toBeVisible();
      await expectNoRawKeys(errPage, 'body');

      const shortPwResp = await anonCtx.request.post(`/invite/${htmlErrInv.code}`, { headers: { Origin: isolatedOrigin, Accept: 'text/html' }, form: { username: 'e2e_shortpw_acct', display_name: 'Short Pw', password: 'short' } });
      expect(shortPwResp.status()).toBe(400);
      expect(shortPwResp.headers()['content-type']).toContain('text/html');
      const shortPwHtml = await shortPwResp.text();
      expect(shortPwHtml.trim().startsWith('{')).toBe(false);
      expect(shortPwHtml).toContain('class="errors"');
      expect(shortPwHtml).toContain('12');
      expect(shortPwHtml).not.toMatch(/\bauth\.(?:password|username)\.[a-z_]+\b/);

      await reloadInvitesPage(adminPage);
      await expect(inviteRowByText(adminPage, 't2-html-err')).toContainText('0 / 2');
      expect(adminErrors).toEqual([]);
    } finally {
      if (anonCtx) await anonCtx.close();
      await adminCtx.close();
    }
  });

  test('T2.2: Permission, cross-user revocation, toggle-off retention & mobile layout boundaries (F3, F4, F5, F6)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    const memACtx = await newIsolatedContext(browser, { clipboard: true });
    const memAPage = await memACtx.newPage();
    const memAErrors = watchErrors(memAPage);
    const memBCtx = await newIsolatedContext(browser);
    const memBPage = await memBCtx.newPage();
    const memBErrors = watchErrors(memBPage);
    const anonCtx = await newIsolatedContext(browser);
    let noClipCtx = null;
    try {
      await setMemberInvitesEnabled(adminPage, true);
      await loginViaForm(memAPage, 'e2e_member_a', MEMBER_PASSWORD);
      await loginViaForm(memBPage, 'e2e_member_b', MEMBER_PASSWORD);

      await gotoInvitesPage(memAPage);
      const invA = await issueInviteViaUI(memAPage, { maxUses: 2, note: 't2-a-only' });

      // 1. Member B cannot see or revoke Member A's invite by code or public_id.
      await gotoInvitesPage(memBPage);
      await expect(memBPage.locator('#invites-list-section')).not.toContainText(invA.code);
      expect((await memBCtx.request.patch('/api/invites', { headers: { Origin: isolatedOrigin }, data: { code: invA.code, action: 'revoke' } })).status()).toBe(404);
      expect((await memBCtx.request.patch('/api/invites', { headers: { Origin: isolatedOrigin }, data: { public_id: invA.public_id, action: 'revoke' } })).status()).toBe(404);

      // 2. Toggle OFF blocks new issuance while retaining view, copy, and revoke of own active codes.
      await setMemberInvitesEnabled(adminPage, false);
      await reloadInvitesPage(memAPage);
      const bootAfterOff = await readBootstrap(memAPage);
      expect(bootAfterOff.member_invites_enabled).toBe(false);
      expect(bootAfterOff.can_issue_invites).toBe(false);
      await assertIssuanceDisabledUI(memAPage);
      expect((await memACtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1, note: 't2-blocked' } })).status()).toBe(403);
      const aRow = inviteRowByText(memAPage, 't2-a-only');
      await expect(aRow).toBeVisible();
      const copied = await copyInviteLink(memAPage, aRow);
      expect(copied).toBe(`${isolatedOrigin}/invite/${invA.code}`);
      await revokeInviteInUI(memAPage, aRow);
      await assertRowStatus(inviteRowByText(memAPage, 't2-a-only'), 'revoked');
      const aApi = await fetchInvitesApi(memACtx);
      expect(aApi.orderedItems.find((it) => it.code === invA.code).status).toBe('revoked');

      // 3. Unauthenticated & non-admin access boundaries.
      const anonSettings = await anonCtx.request.get('/settings/invites', { maxRedirects: 0 });
      expect([302, 303, 401, 403, 404]).toContain(anonSettings.status());
      expect([401, 403, 404]).toContain((await anonCtx.request.get('/api/invites?page=true')).status());
      expect([401, 403, 404]).toContain((await anonCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1 } })).status());
      expect([401, 403, 404]).toContain((await anonCtx.request.patch('/api/invites', { headers: { Origin: isolatedOrigin }, data: { code: invA.code, action: 'revoke' } })).status());
      expect((await memBCtx.request.get('/admin/server')).status()).toBe(404);
      expect((await memBCtx.request.get('/api/admin/server')).status()).toBe(404);
      expect((await memBCtx.request.post('/api/admin/server/invites', { headers: { Origin: isolatedOrigin }, data: { member_invites_enabled: true } })).status()).toBe(404);

      // 4. Invalid toggle payload, invalid PATCH action & double-revoke 404.
      expect((await adminCtx.request.post('/api/admin/server/invites', { headers: { Origin: isolatedOrigin }, data: { member_invites_enabled: 'yes' } })).status()).toBe(400);
      expect((await adminCtx.request.patch('/api/invites', { headers: { Origin: isolatedOrigin }, data: { code: invA.code, action: 'delete' } })).status()).toBe(400);
      const doubleRevokeRes = await adminCtx.request.patch('/api/invites', { headers: { Origin: isolatedOrigin }, data: { code: invA.code, action: 'revoke' } });
      expect(doubleRevokeRes.status()).toBe(404);
      expect((await doubleRevokeRes.json()).error).toBe('invitation_not_found');

      // 5. Mobile viewport (390x844) layout & no overlap.
      await adminPage.setViewportSize({ width: 390, height: 844 });
      await gotoInvitesPage(adminPage);
      await assertBottomPlacement(adminPage, { requireIssueSection: true });
      const mobInv = await issueInviteViaUI(adminPage, { maxUses: 1, note: 't2-mobile-check' });
      const mobRow = inviteRowByText(adminPage, 't2-mobile-check');
      const copyBox = await mobRow.locator('[data-invite-copy]').boundingBox();
      const revokeBox = await mobRow.locator('[data-invite-revoke]').boundingBox();
      expect(copyBox).not.toBeNull();
      expect(revokeBox).not.toBeNull();
      const noOverlap = copyBox.x + copyBox.width <= revokeBox.x + 1 || revokeBox.x + revokeBox.width <= copyBox.x + 1 || copyBox.y + copyBox.height <= revokeBox.y + 1 || revokeBox.y + revokeBox.height <= copyBox.y + 1;
      expect(noOverlap).toBe(true);

      // 6. Clipboard fallback without granted clipboard permissions (no uncaught errors + accessible feedback or prompt fallback).
      noClipCtx = await newIsolatedContext(browser, { storageState: adminStorageState });
      const noClipPage = await noClipCtx.newPage();
      const noClipErrors = watchErrors(noClipPage);
      const noClipDialogs = [];
      noClipPage.on('dialog', async (dlg) => {
        noClipDialogs.push({ type: dlg.type(), defaultValue: dlg.defaultValue() });
        await dlg.dismiss();
      });
      await gotoInvitesPage(noClipPage);
      const noClipRow = inviteRowByText(noClipPage, 't2-mobile-check');
      await expect(noClipRow).toBeVisible();
      const prevNoClipRes = (await noClipPage.locator('#invites-result').innerText().catch(() => '')).trim();
      const prevNoClipToasts = await noClipPage.locator('.claire-toast').count();
      await noClipRow.locator('[data-invite-copy]').click();
      await expect.poll(async () => {
        if (noClipDialogs.length > 0) return true;
        const resultText = (await noClipPage.locator('#invites-result').innerText().catch(() => '')).trim();
        const toastCount = await noClipPage.locator('.claire-toast').count();
        const lastToastText = toastCount > 0 ? (await noClipPage.locator('.claire-toast').last().innerText().catch(() => '')).trim() : '';
        return (toastCount > prevNoClipToasts && lastToastText !== '') || (resultText !== prevNoClipRes && resultText !== '');
      }).toBe(true);
      if (noClipDialogs.length > 0) expect(noClipDialogs[0].defaultValue).toBe(`${isolatedOrigin}/invite/${mobInv.code}`);
      await expectNoRawKeys(noClipPage, '#console-main');
      expect(noClipErrors).toEqual([]);

      await revokeInviteInUI(adminPage, mobRow);
      await assertRowStatus(inviteRowByText(adminPage, 't2-mobile-check'), 'revoked');
      expect((await fetchInvitesApi(adminCtx)).orderedItems.find((it) => it.code === mobInv.code).status).toBe('revoked');
      expect(adminErrors).toEqual([]);
      expect(memAErrors).toEqual([]);
      expect(memBErrors).toEqual([]);
    } finally {
      if (noClipCtx) await noClipCtx.close();
      await anonCtx.close();
      await memBCtx.close();
      await memACtx.close();
      await adminCtx.close();
    }
  });

  test('T3.1: Pairwise F1+F2+F3+F4 (Issue -> bottom list -> copy -> partial use -> revoke remaining -> reject)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState, clipboard: true });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    let anonCtx = null;
    try {
      await gotoInvitesPage(adminPage);
      const inv = await issueInviteViaUI(adminPage, { maxUses: 3, note: 't3-pair-1' });
      await assertBottomPlacement(adminPage, { requireIssueSection: true });
      const row = inviteRowByText(adminPage, 't3-pair-1');
      const copied = await copyInviteLink(adminPage, row);
      expect(copied).toBe(`${isolatedOrigin}/invite/${inv.code}`);

      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const anonPage = await anonCtx.newPage();
      await anonPage.goto(copied, { waitUntil: 'domcontentloaded' });
      await anonPage.locator('#username').fill('e2e_t3_acct1');
      await anonPage.locator('#display_name').fill('T3 Acct One');
      await anonPage.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([anonPage.waitForURL(/\/@e2e_t3_acct1\/?/), anonPage.locator(`form[action="/invite/${inv.code}"] button[type="submit"]`).click()]);
      await anonCtx.close();
      anonCtx = null;

      await reloadInvitesPage(adminPage);
      const refreshedRow = inviteRowByText(adminPage, 't3-pair-1');
      await expect(refreshedRow).toContainText('1 / 3');
      await revokeInviteInUI(adminPage, refreshedRow);
      await assertRowStatus(inviteRowByText(adminPage, 't3-pair-1'), 'revoked');

      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const probe = await anonCtx.newPage();
      const blockedGet = await probe.goto(copied, { waitUntil: 'domcontentloaded' });
      expect(blockedGet.status()).toBe(410);
      await expectNoRawKeys(probe, 'body');
      expect((await anonCtx.request.post(`/invite/${inv.code}`, { headers: { Origin: isolatedOrigin, Accept: 'text/html' }, form: { username: 'e2e_t3_blocked', display_name: 'Blocked', password: MEMBER_PASSWORD } })).status()).toBe(410);
      expect(adminErrors).toEqual([]);
    } finally {
      if (anonCtx) await anonCtx.close();
      await adminCtx.close();
    }
  });

  test('T3.2: Pairwise F6+F5+F2+F3 (Toggle ON -> Member B issues & copies -> signup -> toggle OFF -> Member B blocked -> Admin revokes)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    const memBCtx = await newIsolatedContext(browser, { clipboard: true });
    const memBPage = await memBCtx.newPage();
    const memBErrors = watchErrors(memBPage);
    let anonCtx = null;
    try {
      await setMemberInvitesEnabled(adminPage, true);
      await loginViaForm(memBPage, 'e2e_member_b', MEMBER_PASSWORD);
      await gotoInvitesPage(memBPage);
      const invB = await issueInviteViaUI(memBPage, { maxUses: 2, note: 't3-pair-2' });
      const copied = await copyInviteLink(memBPage, inviteRowByText(memBPage, 't3-pair-2'));

      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const anonPage = await anonCtx.newPage();
      await anonPage.goto(copied, { waitUntil: 'domcontentloaded' });
      await anonPage.locator('#username').fill('e2e_t3_acct2');
      await anonPage.locator('#display_name').fill('T3 Acct Two');
      await anonPage.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([anonPage.waitForURL(/\/@e2e_t3_acct2\/?/), anonPage.locator(`form[action="/invite/${invB.code}"] button[type="submit"]`).click()]);
      await anonCtx.close();
      anonCtx = null;

      await reloadInvitesPage(memBPage);
      await expect(inviteRowByText(memBPage, 't3-pair-2')).toContainText('1 / 2');

      await setMemberInvitesEnabled(adminPage, false);
      expect((await memBCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1, note: 't3-blocked-b' } })).status()).toBe(403);

      await gotoInvitesPage(adminPage);
      await revokeInviteInUI(adminPage, inviteRowByText(adminPage, 't3-pair-2'));
      await assertRowStatus(inviteRowByText(adminPage, 't3-pair-2'), 'revoked');

      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const blocked = await (await anonCtx.newPage()).goto(copied, { waitUntil: 'domcontentloaded' });
      expect(blocked.status()).toBe(410);
      expect(adminErrors).toEqual([]);
      expect(memBErrors).toEqual([]);
    } finally {
      if (anonCtx) await anonCtx.close();
      await memBCtx.close();
      await adminCtx.close();
    }
  });

  test('T3.3: Pairwise F5+F1+F3 (Admin vs Member A vs Member B 3-way scoping isolation & cross-revoke denial)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    const memACtx = await newIsolatedContext(browser);
    const memAPage = await memACtx.newPage();
    const memAErrors = watchErrors(memAPage);
    const memBCtx = await newIsolatedContext(browser);
    const memBPage = await memBCtx.newPage();
    const memBErrors = watchErrors(memBPage);
    try {
      await setMemberInvitesEnabled(adminPage, true);
      await gotoInvitesPage(adminPage);
      const admInv = await issueInviteViaUI(adminPage, { maxUses: 1, note: 't3-adm' });

      await loginViaForm(memAPage, 'e2e_member_a', MEMBER_PASSWORD);
      await gotoInvitesPage(memAPage);
      const memAInv = await issueInviteViaUI(memAPage, { maxUses: 1, note: 't3-mem-a' });

      await loginViaForm(memBPage, 'e2e_member_b', MEMBER_PASSWORD);
      await gotoInvitesPage(memBPage);
      const memBInv = await issueInviteViaUI(memBPage, { maxUses: 1, note: 't3-mem-b' });

      await reloadInvitesPage(memAPage);
      await expect(memAPage.locator('#invites-list-section')).toContainText(memAInv.code);
      await expect(memAPage.locator('#invites-list-section')).not.toContainText(admInv.code);
      await expect(memAPage.locator('#invites-list-section')).not.toContainText(memBInv.code);

      await reloadInvitesPage(memBPage);
      await expect(memBPage.locator('#invites-list-section')).toContainText(memBInv.code);
      await expect(memBPage.locator('#invites-list-section')).not.toContainText(admInv.code);
      await expect(memBPage.locator('#invites-list-section')).not.toContainText(memAInv.code);

      expect((await memACtx.request.patch('/api/invites', { headers: { Origin: isolatedOrigin }, data: { code: memBInv.code, action: 'revoke' } })).status()).toBe(404);
      expect((await memACtx.request.patch('/api/invites', { headers: { Origin: isolatedOrigin }, data: { code: admInv.code, action: 'revoke' } })).status()).toBe(404);

      await reloadInvitesPage(adminPage);
      await expect(adminPage.locator('#invites-list-section')).toContainText(admInv.code);
      await expect(adminPage.locator('#invites-list-section')).toContainText(memAInv.code);
      await expect(adminPage.locator('#invites-list-section')).toContainText(memBInv.code);
      await revokeInviteInUI(adminPage, inviteRowByText(adminPage, 't3-mem-a'));
      await revokeInviteInUI(adminPage, inviteRowByText(adminPage, 't3-mem-b'));
      await revokeInviteInUI(adminPage, inviteRowByText(adminPage, 't3-adm'));
      expect(adminErrors).toEqual([]);
      expect(memAErrors).toEqual([]);
      expect(memBErrors).toEqual([]);
    } finally {
      await setMemberInvitesEnabled(adminPage, false).catch(() => {});
      await memBCtx.close();
      await memACtx.close();
      await adminCtx.close();
    }
  });

  test('T3.4: Pairwise F4+F5+i18n (en/ko bottom layout, expectNoRawKeys, zero Hangul in en, admin isolation, restore locale)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState, locale: 'ko-KR' });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    const memACtx = await newIsolatedContext(browser, { locale: 'ko-KR' });
    const memAPage = await memACtx.newPage();
    const memAErrors = watchErrors(memAPage);
    try {
      await loginViaForm(memAPage, 'e2e_member_a', MEMBER_PASSWORD);
      await setAccountLocale(memAPage, 'en');
      await gotoInvitesPage(memAPage);
      await expect(memAPage.locator('html')).toHaveAttribute('lang', 'en');
      await expectNoRawKeys(memAPage, '#console-main');
      const memEnText = await memAPage.locator('#console-main').innerText();
      expect(/[\uAC00-\uD7A3]/.test(memEnText)).toBe(false);
      const memKeys = await assertMemberIsolation(memAPage);
      expect(memKeys.some((k) => k.startsWith('console.js.invites.'))).toBe(true);
      await assertBottomPlacement(memAPage, { requireIssueSection: false });

      await setAccountLocale(adminPage, 'en');
      await gotoInvitesPage(adminPage);
      await expect(adminPage.locator('html')).toHaveAttribute('lang', 'en');
      await expectNoRawKeys(adminPage, '#console-main');
      const admEnText = await adminPage.locator('#console-main').innerText();
      expect(/[\uAC00-\uD7A3]/.test(admEnText)).toBe(false);
      await assertBottomPlacement(adminPage, { requireIssueSection: true });

      await setAccountLocale(memAPage, 'ko');
      await gotoInvitesPage(memAPage);
      await expect(memAPage.locator('html')).toHaveAttribute('lang', 'ko');
      await expectNoRawKeys(memAPage, '#console-main');
      await assertMemberIsolation(memAPage);
      await assertBottomPlacement(memAPage, { requireIssueSection: false });

      await setAccountLocale(memAPage, '');
      await setAccountLocale(adminPage, '');
      expect(adminErrors).toEqual([]);
      expect(memAErrors).toEqual([]);
    } finally {
      await memACtx.close();
      await adminCtx.close();
    }
  });

  test('S1: Admin onboards a reserved collaborator from scratch via copied link, collaborator logs out and logs back in', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState, clipboard: true });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    let collabCtx = null;
    try {
      await setMemberInvitesEnabled(adminPage, false);
      await gotoInvitesPage(adminPage);
      const inv = await issueInviteViaUI(adminPage, { maxUses: 1, reservedUsername: 'e2e_s1_collab', note: 'Collab onboarding' });
      const copied = await copyInviteLink(adminPage, inviteRowByText(adminPage, 'Collab onboarding'));

      collabCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const collabPage = await collabCtx.newPage();
      const collabErrors = watchErrors(collabPage);
      await collabPage.goto(copied, { waitUntil: 'domcontentloaded' });
      await expect(collabPage.locator('#username')).toHaveValue('e2e_s1_collab');
      await expect(collabPage.locator('#username')).toHaveAttribute('readonly', '');
      await collabPage.locator('#display_name').fill('Collaborator S1');
      await collabPage.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([collabPage.waitForURL(/\/@e2e_s1_collab\/?/), collabPage.locator(`form[action="/invite/${inv.code}"] button[type="submit"]`).click()]);

      await gotoInvitesPage(collabPage);
      await logoutViaMenu(collabPage);
      await loginViaForm(collabPage, 'e2e_s1_collab', MEMBER_PASSWORD);
      await gotoInvitesPage(collabPage);
      expect((await fetchInvitesApi(collabCtx)).orderedItems).toEqual([]);
      expect((await readBootstrap(collabPage)).can_issue_invites).toBe(false);
      await assertIssuanceDisabledUI(collabPage);
      await assertBottomPlacement(collabPage, { requireIssueSection: false });
      await assertMemberIsolation(collabPage);
      expect(collabErrors).toEqual([]);
      expect(adminErrors).toEqual([]);
    } finally {
      if (collabCtx) await collabCtx.close();
      await adminCtx.close();
    }
  });

  test('S2: Delegated community growth (Admin enables toggle, Member A invites Peer, Peer signs up, sees empty list, issues own)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    const memACtx = await newIsolatedContext(browser, { clipboard: true });
    const memAPage = await memACtx.newPage();
    const memAErrors = watchErrors(memAPage);
    let peerCtx = null;
    try {
      await setMemberInvitesEnabled(adminPage, true);
      await loginViaForm(memAPage, 'e2e_member_a', MEMBER_PASSWORD);
      await gotoInvitesPage(memAPage);
      const s2Inv = await issueInviteViaUI(memAPage, { maxUses: 2, note: 'S2 peer' });
      const copied = await copyInviteLink(memAPage, inviteRowByText(memAPage, 'S2 peer'));

      peerCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      const peerPage = await peerCtx.newPage();
      const peerErrors = watchErrors(peerPage);
      await peerPage.goto(copied, { waitUntil: 'domcontentloaded' });
      await peerPage.locator('#username').fill('e2e_s2_peer');
      await peerPage.locator('#display_name').fill('S2 Peer');
      await peerPage.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([peerPage.waitForURL(/\/@e2e_s2_peer\/?/), peerPage.locator(`form[action="/invite/${s2Inv.code}"] button[type="submit"]`).click()]);

      await gotoInvitesPage(peerPage);
      expect((await fetchInvitesApi(peerCtx)).orderedItems).toEqual([]);
      await expect(peerPage.locator('#invites-list-section')).not.toContainText(s2Inv.code);
      await assertBottomPlacement(peerPage, { requireIssueSection: true });
      const peerOwn = await issueInviteViaUI(peerPage, { maxUses: 1, note: 'S2 peer own' });
      const peerOwnRow = inviteRowByText(peerPage, 'S2 peer own');
      await expect(peerOwnRow).toContainText(peerOwn.code);
      await assertRowStatus(peerOwnRow, 'active');

      await reloadInvitesPage(memAPage);
      await expect(inviteRowByText(memAPage, 'S2 peer')).toContainText('1 / 2');
      await revokeInviteInUI(memAPage, inviteRowByText(memAPage, 'S2 peer'));
      await assertRowStatus(inviteRowByText(memAPage, 'S2 peer'), 'revoked');
      expect(peerErrors).toEqual([]);
      expect(memAErrors).toEqual([]);
      expect(adminErrors).toEqual([]);
    } finally {
      if (peerCtx) await peerCtx.close();
      await memACtx.close();
      await adminCtx.close();
    }
  });

  test('S3: Leaked multi-use link emergency revocation by issuer (max_uses=5, participant 1 joins, issuer revokes, participant 2 blocked)', async ({ browser }) => {
    const memACtx = await newIsolatedContext(browser, { clipboard: true });
    const memAPage = await memACtx.newPage();
    const memAErrors = watchErrors(memAPage);
    let p1Ctx = null;
    let p2Ctx = null;
    try {
      await loginViaForm(memAPage, 'e2e_member_a', MEMBER_PASSWORD);
      await gotoInvitesPage(memAPage);
      const workshopInv = await issueInviteViaUI(memAPage, { maxUses: 5, note: 'S3 workshop' });
      const copied = await copyInviteLink(memAPage, inviteRowByText(memAPage, 'S3 workshop'));

      p1Ctx = await newIsolatedContext(browser, { locale: 'en-US' });
      const p1Page = await p1Ctx.newPage();
      await p1Page.goto(copied, { waitUntil: 'domcontentloaded' });
      await p1Page.locator('#username').fill('e2e_s3_p1');
      await p1Page.locator('#display_name').fill('Workshop P1');
      await p1Page.locator('#password').fill(MEMBER_PASSWORD);
      await Promise.all([p1Page.waitForURL(/\/@e2e_s3_p1\/?/), p1Page.locator(`form[action="/invite/${workshopInv.code}"] button[type="submit"]`).click()]);
      await p1Ctx.close();
      p1Ctx = null;

      await reloadInvitesPage(memAPage);
      const row = inviteRowByText(memAPage, 'S3 workshop');
      await expect(row).toContainText('1 / 5');
      await revokeInviteInUI(memAPage, row);
      await assertRowStatus(inviteRowByText(memAPage, 'S3 workshop'), 'revoked');

      p2Ctx = await newIsolatedContext(browser, { locale: 'en-US' });
      const p2Page = await p2Ctx.newPage();
      const blocked = await p2Page.goto(copied, { waitUntil: 'domcontentloaded' });
      expect(blocked.status()).toBe(410);
      await expectNoRawKeys(p2Page, 'body');
      expect((await p2Ctx.request.post(`/invite/${workshopInv.code}`, { headers: { Origin: isolatedOrigin, Accept: 'text/html' }, form: { username: 'e2e_s3_p2', display_name: 'Workshop P2', password: MEMBER_PASSWORD } })).status()).toBe(410);
      expect(memAErrors).toEqual([]);
    } finally {
      if (p1Ctx) await p1Ctx.close();
      if (p2Ctx) await p2Ctx.close();
      await memACtx.close();
    }
  });

  test('S4: Administrator incident response (Admin revokes peer active invite and locks down member issuance)', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState });
    const adminPage = await adminCtx.newPage();
    const adminErrors = watchErrors(adminPage);
    const peerCtx = await newIsolatedContext(browser);
    const peerPage = await peerCtx.newPage();
    const peerErrors = watchErrors(peerPage);
    let anonCtx = null;
    try {
      await gotoInvitesPage(adminPage);
      const peerRow = inviteRowByText(adminPage, 'S2 peer own');
      await expect(peerRow).toBeVisible();
      const revokeRes = await revokeInviteInUI(adminPage, peerRow);
      expect(revokeRes.status).toBe('revoked');
      await assertRowStatus(inviteRowByText(adminPage, 'S2 peer own'), 'revoked');

      await setMemberInvitesEnabled(adminPage, false);

      await loginViaForm(peerPage, 'e2e_s2_peer', MEMBER_PASSWORD);
      await gotoInvitesPage(peerPage);
      await assertRowStatus(inviteRowByText(peerPage, 'S2 peer own'), 'revoked');
      await assertIssuanceDisabledUI(peerPage);
      const peerApi = await fetchInvitesApi(peerCtx);
      const peerOwnItem = peerApi.orderedItems.find((it) => it.note === 'S2 peer own');
      expect(peerOwnItem.status).toBe('revoked');
      expect((await readBootstrap(peerPage)).can_issue_invites).toBe(false);
      expect((await peerCtx.request.post('/api/invites', { headers: { Origin: isolatedOrigin }, data: { max_uses: 1, note: 's4-blocked' } })).status()).toBe(403);

      anonCtx = await newIsolatedContext(browser, { locale: 'en-US' });
      expect((await (await anonCtx.newPage()).goto(`${isolatedOrigin}/invite/${peerOwnItem.code}`, { waitUntil: 'domcontentloaded' })).status()).toBe(410);
      expect(adminErrors).toEqual([]);
      expect(peerErrors).toEqual([]);
    } finally {
      if (anonCtx) await anonCtx.close();
      await peerCtx.close();
      await adminCtx.close();
    }
  });

  test('S5: Mobile administrator triage in Korean (ko-KR, 390x844) via #console-mob-menu', async ({ browser }) => {
    const adminCtx = await newIsolatedContext(browser, { storageState: adminStorageState, clipboard: true, locale: 'ko-KR', viewport: { width: 390, height: 844 } });
    const page = await adminCtx.newPage();
    const errors = watchErrors(page);
    try {
      await page.goto('/settings/account', { waitUntil: 'domcontentloaded' });
      await expect(page.locator('#console-mob-menu')).toBeVisible();
      await page.locator('#console-mob-menu').click();
      const drawer = page.locator('#console-nav-drawer');
      await expect(drawer).toBeVisible();
      await waitForInvitesAfter(page, () => drawer.locator('a[data-nav-section="invites"][href="/settings/invites"]').click());
      await expect(page).toHaveURL(/\/settings\/invites/);
      await expect(page.locator('html')).toHaveAttribute('lang', 'ko');
      await expectNoRawKeys(page, '#console-main');

      const s5Inv = await issueInviteViaUI(page, { maxUses: 2, note: 's5-mobile-ko' });
      await assertBottomPlacement(page, { requireIssueSection: true });
      const row = inviteRowByText(page, 's5-mobile-ko');
      await assertRowStatus(row, 'active');
      const copied = await copyInviteLink(page, row);
      expect(copied).toBe(`${isolatedOrigin}/invite/${s5Inv.code}`);
      await revokeInviteInUI(page, row);
      await assertRowStatus(inviteRowByText(page, 's5-mobile-ko'), 'revoked');
      expect((await fetchInvitesApi(adminCtx)).orderedItems.find((it) => it.code === s5Inv.code).status).toBe('revoked');
      expect(errors).toEqual([]);
    } finally {
      await adminCtx.close();
    }
  });
});
