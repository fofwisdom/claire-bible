// Network-mode root / Calm Federated Observatory, 4-item appbar, peer hovercards, and unified navigation on an isolated server instance.
const { execFileSync, spawn } = require('node:child_process');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const { test, expect } = require('@playwright/test');
const { expectNoRawKeys, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

const REPO_ROOT = path.resolve(__dirname, '..');
const HOME_RAW_KEY = /(?:^|[\s"'(>:])((?:about|home_obs|home_root|directory|themes|kb\.pub|pipeline\.neon|lang\.name)\.[a-z0-9_]+(?:\.[a-z0-9_]+)*)/;

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
  expect(found ? found[1] : null, 'raw home/observatory i18n key').toBeNull();
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
    'from claire.federation.knowledge import known_servers',
    'from claire.store import accounts as accounts_store, db as dbm, federation as federation_store, kb_collab, themes as themes_store',
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
    '    known_servers.ensure_schema(conn)',
    '    known_servers.note_server(conn, domain="peer-a.example", source="admin", now=now, added_by=1)',
    '    known_servers.record_read(conn, "peer-a.example", server_actor_iri="https://peer-a.example/ap/server", software_name="claire-bible", software_version="9.9.9-secret-ver", shared_kb_count=2, now=now)',
    '    cur = conn.execute("INSERT INTO kf_remote_knowledge_bases (iri, domain, url, label, description, knowledge_type, document_count, entity_count, relation_count, signature, payload, first_seen_at, seen_at, changed_at) VALUES (\'https://peer-a.example/ap/kb/501\', \'peer-a.example\', \'https://peer-a.example/kb/501/\', \'Peer A Atlas\', \'Remote atlas\', \'real\', 12, 40, 15, NULL, \'{}\', ?, ?, ?)", (now, now, now))',
    '    themes_store.set_computed_remote_member(conn, theme_id=1, remote_kb_id=int(cur.lastrowid), score=0.92)',
    '    kb_collab.add_contribution(conn, kb_id=10, document_id="doc-pub-1", origin="curation", contributor_account_id=None, curator_account_id=1, state="visible", approved_at=now, approved_by_account_id=1, remote={"source_kb_iri": "https://peer-a.example/ap/kb/501", "source_iri": "https://peer-a.example/doc/1", "root_iri": "https://peer-a.example/doc/1", "credit_iri": None}, now=now)',
    '    known_servers.note_server(conn, domain="blocked-peer.example", source="admin", now=now, added_by=1)',
    '    known_servers.record_read(conn, "blocked-peer.example", server_actor_iri="https://blocked-peer.example/ap/server", software_name="claire-bible", software_version="8.8.8-blocked-ver", shared_kb_count=4, now=now)',
    '    federation_store.add_domain_block(conn, "blocked-peer.example", created_by=1, note="blocked", now=now)',
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

test('network-mode / renders Calm Federated Observatory, peer hovercard, 4-item appbar, and unified navigation', async ({ browser }) => {
  const context = await browser.newContext({ baseURL: isolatedBaseUrl, serviceWorkers: 'block', locale: 'ko-KR' });
  const page = await context.newPage();
  const errors = watchErrors(page);
  try {
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.goto('/', { waitUntil: 'domcontentloaded' });

    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    const navLinks = page.locator('header.appbar .pub-appbar-nav a');
    await expect(navLinks).toHaveCount(4);
    await expect(page.locator('header.appbar .pub-appbar-nav a[href="/"][aria-current="page"]')).toBeVisible();
    await expect(page.locator('header.appbar .pub-appbar-nav a[href="/themes"]')).toBeVisible();
    await expect(page.locator('header.appbar .pub-appbar-nav a[href="/directory"]')).toBeVisible();
    await expect(page.locator('header.appbar .pub-appbar-nav a[href="/about"]')).toBeVisible();
    await expect(page.locator('header.appbar a[href="/kb"]')).toHaveCount(0);
    await expect(page.locator('header.appbar a[href="/reference"]')).toHaveCount(0);
    await expect(page.locator('.claire-pubnav:visible')).toHaveCount(0);

    await expect(page.locator('#home-observatory')).toBeVisible();
    await expect(page.locator('#home-observatory .obs-flow-grid')).toBeVisible();
    await expect(page.locator('#home-observatory .obs-reflux')).toBeVisible();
    await expect(page.locator('.p4-theatre')).toHaveCount(0);
    await expect(page.locator('.p4-hero-neon')).toHaveCount(0);

    const peerNode = page.locator('#home-observatory a[data-obs-peer="peer-a.example"]');
    await expect(peerNode).toBeVisible();
    await expect(page.locator('body')).not.toContainText('blocked-peer.example');
    await expect(page.locator('body')).not.toContainText('9.9.9-secret-ver');
    await expect(page.locator('#home-themes')).toContainText('AI Architecture');
    await expect(page.locator('#home-themes')).toContainText('Public Shared KB');
    await expect(page.locator('#home-directory-link a[href="/directory"]')).toBeVisible();

    // Hover and focus reveal the declarative #obs-hc-peer-0 hovercard.
    const hovercard = page.locator('#obs-hc-peer-0');
    await peerNode.hover();
    await expect(hovercard).toBeVisible();
    await expect(hovercard).toContainText('peer-a.example');
    await expect(hovercard).toContainText('Peer A Atlas');
    await page.keyboard.press('Escape');
    await peerNode.focus();
    await expect(hovercard).toBeVisible();
    await page.keyboard.press('Escape');

    await expect(page.locator('footer.about-footer a[href="/reference"]')).toContainText('API 문서');
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

    // Navigation: / -> /themes -> /directory -> /about -> /
    await page.setViewportSize({ width: 1360, height: 900 });
    await page.locator('header.appbar .pub-appbar-nav a[href="/themes"]').click();
    await expect(page).toHaveURL(/\/themes$/);
    await expect(page.locator('header.appbar .pub-appbar-nav a[href="/themes"][aria-current="page"]')).toBeVisible();
    await expect(page.locator('.claire-pubnav:visible')).toHaveCount(0);
    await expect(page.locator('#public-kb-root .obs-page-head')).toBeVisible();
    await expect(page.locator('#public-kb-root .obs-view-switch a[href="/themes"][aria-current="page"]')).toBeVisible();
    await expect(page.locator('#public-kb-root .obs-view-switch a[href="/kb"]')).toBeVisible();
    await expect(page.locator('#public-kb-root .obs-catalog-card')).toContainText('AI Architecture');
    await expect(page.locator('#public-kb-root .obs-catalog-peers')).toContainText('peer-a.example');
    await expect(page.locator('body')).not.toContainText('blocked-peer.example');
    await expect(page.locator('footer.about-footer')).toBeVisible();
    await expect(page.locator('footer.about-footer a[href="/reference"]')).toHaveCount(0);
    await expectNoHomeRawKeys(page);
    await expectZeroOverflow(page, '/themes desktop light');
    await page.locator('#themebtn').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expectZeroOverflow(page, '/themes desktop dark');
    await page.setViewportSize({ width: 390, height: 844 });
    await expectZeroOverflow(page, '/themes mobile dark');
    await page.locator('#themebtn').click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expectZeroOverflow(page, '/themes mobile light');
    await page.setViewportSize({ width: 1360, height: 900 });

    await page.locator('header.appbar .pub-appbar-nav a[href="/directory"]').click();
    await expect(page).toHaveURL(/\/directory$/);
    await expect(page.locator('header.appbar .pub-appbar-nav a[href="/directory"][aria-current="page"]')).toBeVisible();
    await expect(page.locator('.account-card')).toContainText('Curator Ada');

    await page.locator('header.appbar .pub-appbar-nav a[href="/about"]').click();
    await expect(page).toHaveURL(/\/about$/);
    await expect(page.locator('header.appbar .pub-appbar-nav a[href="/about"][aria-current="page"]')).toBeVisible();
    await expect(page.locator('#about-top')).toBeVisible();

    await page.locator('header.appbar .pub-appbar-nav a[href="/"]').click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator('#home-observatory')).toBeVisible();

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

    const workspaceCta = page.locator('.obs-ws-cta[href="/@curator_ada/"]');
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
