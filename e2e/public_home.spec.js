// Network-mode root / page sharing /about UI and look-and-feel on an isolated server instance.
const { execFileSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { test, expect } = require('@playwright/test');
const { expectNoRawKeys, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

const REPO_ROOT = path.resolve(__dirname, '..');
const HOME_RAW_KEY = /(?:^|[\s"'(>:])((?:about|home_root|directory|themes|kb\.pub|pipeline\.neon|lang\.name)\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)/;

let serverProc = null;
let tmpDir = null;
let isolatedBaseUrl = null;
let ownerSessionToken = null;
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
    // Fall through to system python lookup.
  }
  for (const candidate of ['python3', 'python']) {
    try {
      execFileSync(candidate, ['--version'], { stdio: 'ignore' });
      return candidate;
    } catch (_err) {
      // Try next binary name.
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

async function expectNoHomeRawKeys(page) {
  await expectNoRawKeys(page);
  const text = await page.evaluate(() => document.body.innerText || '');
  const found = HOME_RAW_KEY.exec(text);
  expect(found ? found[1] : null, 'raw home/about i18n key').toBeNull();
}

async function expectZeroOverflow(page, label) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow, `${label} horizontal overflow`).toBeLessThanOrEqual(1);
}

test.beforeAll(async () => {
  const py = resolvePythonExecutable();
  const port = await allocateFreePort();
  tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'claire-e2e-public-home-'));
  const dbPath = path.join(tmpDir, 'claire.db');
  const pluginsDir = path.join(tmpDir, 'plugins');
  fs.mkdirSync(pluginsDir, { recursive: true });
  isolatedBaseUrl = `http://127.0.0.1:${port}`;

  const seedScript = [
    'import time',
    'from pathlib import Path',
    'import claire_bible.bootstrap',
    'from claire.store import accounts as accounts_store, db as dbm, themes as themes_store',
    'claire_bible.bootstrap.configure()',
    `db_file = Path(${JSON.stringify(dbPath)})`,
    'conn = dbm.connect(db_file)',
    'try:',
    '    dbm.init_db(conn)',
    '    now = time.time()',
    '    store = accounts_store.AccountStore(conn)',
    '    store.complete_setup(1, username="curator_ada", display_name="Curator Ada")',
    '    conn.execute("UPDATE accounts SET profile_public = 1 WHERE id = 1")',
    '    store.set_setting("mode", "network", updated_by=1)',
    '    conn.execute("INSERT INTO knowledge_bases (id, seq, number, label, account_id, is_personal, is_public, knowledge_type, created_at, updated_at) VALUES (10, 10, 1001, \'Public Shared KB\', 1, 0, 1, \'real\', ?, ?)", (now, now))',
    '    conn.execute("INSERT INTO themes (id, number, label, knowledge_type, state, top_entities, last_activity_at, created_at, updated_at) VALUES (1, 2001, \'AI Architecture\', \'real\', \'active\', \'[\\"vLLM\\", \\"PagedAttention\\"]\', ?, ?, ?)", (now, now, now))',
    '    themes_store.add_theme_member(conn, theme_id=1, kb_id=10, source="computed", now=now)',
    '    conn.execute("INSERT INTO documents (id, kb_id, url, title, raw_text, hidden, extraction_status) VALUES (\'doc-pub-1\', 10, \'https://example.com/p1\', \'Public Extracted Doc\', \'text\', 0, \'extracted\')")',
    '    conn.execute("INSERT INTO entities (id, kb_id, type, name, norm_name, provisional, created_at, updated_at) VALUES (\'ent-pub-1\', 10, \'Concept\', \'Public Concept\', \'public concept\', 0, ?, ?)", (now, now))',
    '    token = accounts_store.create_account_session(conn, account_id=1, scope="owner")',
    '    conn.commit()',
    '    print(token)',
    'finally:',
    '    conn.close()',
  ].join('\n');

  const env = {
    ...process.env,
    PYTHONPATH: process.env.PYTHONPATH ? `src:${process.env.PYTHONPATH}` : 'src',
    CLAIRE_DB_PATH: dbPath,
    CLAIRE_DATA_DIR: tmpDir,
    CLAIRE_INJECT_PORT: String(port),
    CLAIRE_FQDN: `127.0.0.1:${port}`,
    CLAIRE_ALLOW_INSECURE_HTTP: 'true',
    CLAIRE_ENVIRONMENT: 'development',
    CLAIRE_BUILD_COMMIT: '7a3f9c2deadbeef',
    CLAIRE_PLUGINS_DIR: pluginsDir,
  };

  ownerSessionToken = execFileSync(py, ['-c', seedScript], { cwd: REPO_ROOT, env, encoding: 'utf8' }).trim().split(/\r?\n/).pop();
  expect(ownerSessionToken).toBeTruthy();

  serverProc = spawn(py, ['-m', 'claire_bible.cli', 'serve-api'], { cwd: REPO_ROOT, env, stdio: ['ignore', 'pipe', 'pipe'] });
  serverProc.stdout.on('data', (chunk) => serverLogs.push(String(chunk)));
  serverProc.stderr.on('data', (chunk) => serverLogs.push(String(chunk)));
  await waitForHealth(`${isolatedBaseUrl}/health`, serverProc, 20_000);
});

test.afterAll(async () => {
  await stopIsolatedServer();
  if (tmpDir) {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    tmpDir = null;
  }
});

test('network-mode / renders shared /about look-and-feel anonymously across viewports and themes', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: isolatedBaseUrl, serviceWorkers: 'block', locale: 'ko-KR' });
  const page = await context.newPage();
  const errors = watchErrors(page);
  try {
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(page.locator('header.appbar .pub-appbar-nav a[href="/"][aria-current="page"]')).toBeVisible();
    await expect(page.locator('header.appbar a[href="/reference"]')).toHaveCount(0);

    await expect(page.locator('#home-top.about-card.p4-hero-neon')).toBeVisible();
    await expect(page.locator('#home-top .p4-emblem')).toBeVisible();
    await expect(page.locator('#home-top .about-kpi')).toHaveCount(4);
    await expect(page.locator('#home-top')).toContainText('build 7a3f9c2');
    await expect(page.locator('#home-top .about-meta-strip')).toContainText('지식 관리자');
    await expect(page.locator('#home-top .about-meta-strip')).toContainText('Curator Ada');

    await expect(page.locator('#public-home-showcase .claire-pubnav')).toBeVisible();
    await expect(page.locator('#home-themes')).toContainText('AI Architecture');
    await expect(page.locator('#home-themes')).toContainText('Public Shared KB');
    await expect(page.locator('#home-accounts')).toContainText('Curator Ada');
    await expect(page.locator('#home-accounts')).toContainText('@curator_ada');

    await expect(page.locator('footer.about-footer a[href="/reference"]')).toContainText('API 문서');
    await expect(page.locator('footer.about-footer')).not.toContainText('지식 관리자');
    await expectNoHomeRawKeys(page);
    await expectZeroOverflow(page, 'desktop light');

    // Theme toggle: light -> dark
    await page.locator('#themebtn').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expectZeroOverflow(page, 'desktop dark');

    // Mobile 390x844 in dark and light themes
    await page.setViewportSize({ width: 390, height: 844 });
    await expectZeroOverflow(page, 'mobile dark');
    await page.locator('#themebtn').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expectZeroOverflow(page, 'mobile light');

    // Navigation between / and /about
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.locator('header.appbar .pub-appbar-nav a[href="/about"]').click();
    await expect(page).toHaveURL(/\/about$/);
    await page.locator('header.appbar .pub-appbar-nav a[href="/"]').click();
    await expect(page).toHaveURL(/\/$/);

    expect(errors).toEqual([]);
  } finally {
    await context.close();
  }
});

test('network-mode / renders workspace CTA and authenticated appbar controls when signed in', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: isolatedBaseUrl, serviceWorkers: 'block', locale: 'ko-KR' });
  await context.addCookies([{ name: 'claire_session', value: ownerSessionToken, url: isolatedBaseUrl }]);
  const page = await context.newPage();
  const errors = watchErrors(page);
  try {
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    const workspaceCta = page.locator('.home-hero-actions a[href="/@curator_ada/"]');
    await expect(workspaceCta).toBeVisible();
    await expect(workspaceCta).toContainText('내 작업 공간');
    await expect(page.locator('#homelinkbtn')).toHaveAttribute('href', '/@curator_ada/');
    await expect(page.locator('header.appbar a[href="/login"]')).toHaveCount(0);
    await expect(page.locator('#pop-hamburger-menu #menu-logout-form[action="/logout"]')).toBeAttached();
    await expect(page.locator('#pop-hamburger-menu #menu-logout-form[action="/logout"]')).not.toHaveAttribute('hidden', '');
    await expectNoHomeRawKeys(page);
    expect(errors).toEqual([]);
  } finally {
    await context.close();
  }
});
