// Role-filtered /reference Scalar UI, OpenAPI 3.1 endpoint gating, and /api/v1 round-trip verification.
const { execFileSync, spawn } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { test, expect } = require('@playwright/test');
const { watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

const REPO_ROOT = path.resolve(__dirname, '..');

let serverProc = null;
let tmpDir = null;
let isolatedOrigin = null;
let isolatedBaseUrl = null;
let memberToken = null;
let adminToken = null;
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
    // Fall back to system Python when git metadata is absent.
  }
  for (const candidate of ['python3', 'python']) {
    try {
      execFileSync(candidate, ['--version'], { stdio: 'ignore' });
      return candidate;
    } catch (_err) {
      // Try next Python binary name.
    }
  }
  throw new Error('Unable to locate Python executable');
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
      // Server is still starting.
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

async function readScalarSources(page) {
  const cfgRaw = await page.locator('#api-reference-config, #api-reference').getAttribute('data-configuration');
  expect(cfgRaw).toBeTruthy();
  const cfg = JSON.parse(cfgRaw);
  return (cfg.sources || []).map((src) => src.url);
}

test.describe.serial('/reference role filtering, OpenAPI specs, and /api/v1 round-trip', () => {
  test.beforeAll(async () => {
    test.setTimeout(90_000);
    const py = resolvePythonExecutable();
    const port = await allocateFreePort();
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'claire-ref-api-v1-e2e-'));
    const dbPath = path.join(tmpDir, 'ref_v1_isolated.db');
    const pluginsDir = path.join(tmpDir, 'e2e-plugins');
    isolatedOrigin = `http://127.0.0.1:${port}`;
    isolatedBaseUrl = `${isolatedOrigin}/`;
    const env = {
      ...process.env,
      PYTHONPATH: process.env.PYTHONPATH ? `src:.:${process.env.PYTHONPATH}` : 'src:.',
      CLAIRE_DB_PATH: dbPath,
      CLAIRE_INJECT_PORT: String(port),
      CLAIRE_FQDN: `127.0.0.1:${port}`,
      CLAIRE_ENVIRONMENT: 'development',
      CLAIRE_ALLOW_INSECURE_HTTP: 'true',
      CLAIRE_SECRET_KEY: crypto.randomBytes(32).toString('hex'),
      CLAIRE_PLUGINS_DIR: pluginsDir,
    };

    execFileSync(py, [path.join(REPO_ROOT, 'e2e', 'seed_db.py')], { cwd: REPO_ROOT, env, stdio: 'pipe' });
    const seedOut = execFileSync(
      py,
      [
        '-c',
        [
          'import json',
          'import claire_bible.bootstrap',
          'claire_bible.bootstrap.configure()',
          'from claire.core.config import Settings',
          'from claire.store import accounts as accounts_store, db as dbm',
          'from claire.store.knowledge_base import KnowledgeBaseManager',
          'from tests.auth_helpers import ensure_test_account, issue_session_token',
          's = Settings(_env_file=None)',
          'conn = dbm.connect_existing(s.db_file)',
          'try:',
          '    accounts_store.AccountStore(conn).set_setting("mode", "network", updated_by=1)',
          'finally:',
          '    conn.close()',
          'member_id = ensure_test_account(s, account_id=2, username="e2erefmember", role="member")',
          'KnowledgeBaseManager(s).define_kb("Member Personal", is_personal=True, is_public=False, account_id=member_id)',
          'print(json.dumps({"admin": issue_session_token(s, account_id=1, scope="owner"), "member": issue_session_token(s, account_id=member_id, scope="owner")}))',
        ].join('\n'),
      ],
      { cwd: REPO_ROOT, env, encoding: 'utf8' },
    ).trim();
    const tokens = JSON.parse(seedOut);
    adminToken = tokens.admin;
    memberToken = tokens.member;

    serverProc = spawn(py, ['-m', 'claire_bible.cli', 'serve-api'], { cwd: REPO_ROOT, env, stdio: ['ignore', 'pipe', 'pipe'] });
    serverProc.stdout.on('data', (chunk) => {
      serverLogs.push(chunk.toString('utf8'));
      if (serverLogs.length > 200) serverLogs.shift();
    });
    serverProc.stderr.on('data', (chunk) => {
      serverLogs.push(chunk.toString('utf8'));
      if (serverLogs.length > 200) serverLogs.shift();
    });
    await waitForHealth(`${isolatedOrigin}/api/v1/health`, serverProc, 30_000);
  });

  test.afterAll(async () => {
    await stopIsolatedServer();
    if (tmpDir) fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  test('anonymous caller sees only v1 tab on /reference and gets 404 on privileged specs', async ({ browser }) => {
    const context = await browser.newContext({ baseURL: isolatedBaseUrl, serviceWorkers: 'block', locale: 'en-US' });
    const page = await context.newPage();
    const errors = watchErrors(page);
    try {
      const refRes = await page.goto('/reference', { waitUntil: 'domcontentloaded' });
      expect(refRes.status()).toBe(200);
      expect(refRes.headers()['cache-control']).toBe('private, no-store');
      expect(await readScalarSources(page)).toEqual(['/openapi/v1.json']);
      await expect(page.locator('#scalar-reference-root')).toContainText('Claire Bible', { timeout: 15_000 });

      const healthRes = await context.request.get('/api/v1/health');
      expect(healthRes.status()).toBe(200);
      expect((await healthRes.json()).ok).toBe(true);

      const v1SpecRes = await context.request.get('/openapi/v1.json');
      expect(v1SpecRes.status()).toBe(200);
      expect((await v1SpecRes.json()).openapi).toBe('3.1.0');

      expect((await context.request.get('/openapi/account.json')).status()).toBe(404);
      expect((await context.request.get('/openapi/admin.json')).status()).toBe(404);
      expect((await context.request.get('/api/v1/me')).status()).toBe(401);
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  });

  test('member owner sees v1 + account tabs and completes authenticated /api/v1/kbs round-trip', async ({ browser }) => {
    const context = await browser.newContext({ baseURL: isolatedBaseUrl, serviceWorkers: 'block', locale: 'en-US' });
    await context.addCookies([{ name: 'claire_session', value: memberToken, url: isolatedOrigin, httpOnly: true, sameSite: 'Lax' }]);
    const page = await context.newPage();
    const errors = watchErrors(page);
    try {
      const refRes = await page.goto('/reference', { waitUntil: 'domcontentloaded' });
      expect(refRes.status()).toBe(200);
      expect(await readScalarSources(page)).toEqual(['/openapi/v1.json', '/openapi/account.json']);
      const docSwitcher = page.getByRole('button', { name: 'Public API v1' });
      await expect(docSwitcher).toBeVisible({ timeout: 15_000 });
      await docSwitcher.click();
      await expect(page.getByText('Account & Workspace BFF')).toBeVisible();
      await expect(page.getByText('Admin & Operations')).toHaveCount(0);

      const accSpecRes = await context.request.get('/openapi/account.json');
      expect(accSpecRes.status()).toBe(200);
      expect(accSpecRes.headers()['cache-control']).toBe('private, no-store');
      expect((await context.request.get('/openapi/admin.json')).status()).toBe(404);

      const meRes = await context.request.get('/api/v1/me');
      expect(meRes.status()).toBe(200);
      const meJson = await meRes.json();
      expect(meJson.scope).toBe('owner');
      expect(meJson.is_admin).toBe(false);

      const listRes = await context.request.get('/api/v1/kbs');
      expect(listRes.status()).toBe(200);
      const listJson = await listRes.json();
      expect(listJson.personal_kb.is_personal).toBe(true);
      expect(Array.isArray(listJson.items)).toBe(true);

      const createRes = await context.request.post('/api/v1/kbs', {
        headers: { Origin: isolatedOrigin, 'Content-Type': 'application/json' },
        data: { label: 'E2E V1 Shared KB', description: 'Initial E2E description', knowledge_type: 'real' },
      });
      expect(createRes.status()).toBe(201);
      const created = await createRes.json();
      expect(created.knowledge_base.label).toBe('E2E V1 Shared KB');
      const kbNumber = String(created.knowledge_base.number);

      const getRes = await context.request.get(`/api/v1/kbs/${kbNumber}`);
      expect(getRes.status()).toBe(200);
      expect((await getRes.json()).knowledge_base.number).toBe(kbNumber);

      const patchRes = await context.request.patch(`/api/v1/kbs/${kbNumber}`, {
        headers: { Origin: isolatedOrigin, 'Content-Type': 'application/json' },
        data: { description: 'Updated via /api/v1 E2E' },
      });
      expect(patchRes.status()).toBe(200);
      expect((await patchRes.json()).knowledge_base.description).toBe('Updated via /api/v1 E2E');
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  });

  test('admin session sees v1 + account + admin tabs on /reference and 200 on /openapi/admin.json', async ({ browser }) => {
    const context = await browser.newContext({ baseURL: isolatedBaseUrl, serviceWorkers: 'block', locale: 'en-US' });
    await context.addCookies([{ name: 'claire_session', value: adminToken, url: isolatedOrigin, httpOnly: true, sameSite: 'Lax' }]);
    const page = await context.newPage();
    const errors = watchErrors(page);
    try {
      const refRes = await page.goto('/reference', { waitUntil: 'domcontentloaded' });
      expect(refRes.status()).toBe(200);
      expect(await readScalarSources(page)).toEqual(['/openapi/v1.json', '/openapi/account.json', '/openapi/admin.json']);
      const docSwitcher = page.getByRole('button', { name: 'Public API v1' });
      await expect(docSwitcher).toBeVisible({ timeout: 15_000 });
      await docSwitcher.click();
      await expect(page.getByText('Account & Workspace BFF')).toBeVisible();
      await expect(page.getByText('Admin & Operations')).toBeVisible();

      const accSpecRes = await context.request.get('/openapi/account.json');
      expect(accSpecRes.status()).toBe(200);
      const admSpecRes = await context.request.get('/openapi/admin.json');
      expect(admSpecRes.status()).toBe(200);
      expect(admSpecRes.headers()['cache-control']).toBe('private, no-store');

      const meRes = await context.request.get('/api/v1/me');
      expect(meRes.status()).toBe(200);
      const meJson = await meRes.json();
      expect(meJson.scope).toBe('owner');
      expect(meJson.is_admin).toBe(true);
      expect(errors).toEqual([]);
    } finally {
      await context.close();
    }
  });
});
