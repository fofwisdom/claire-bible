const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { defineConfig } = require('@playwright/test');

const repoRoot = path.resolve(__dirname, '..');
const port = String(process.env.CLAIRE_E2E_PORT || process.env.CLAIRE_INJECT_PORT || '8766');
const defaultUrl = `http://127.0.0.1:${port}/`;
const baseURL = process.env.CLAIRE_E2E_BASE_URL || defaultUrl;
const dbPath = process.env.CLAIRE_DB_PATH || 'data/e2e.db';

/** Resolve Python interpreter command so git worktrees reuse an existing venv instead of building a duplicate venv. */
function resolvePythonPrefix() {
  if (process.env.CLAIRE_E2E_PYTHON) return process.env.CLAIRE_E2E_PYTHON;
  const localVenv = path.join(repoRoot, '.venv', 'bin', 'python');
  if (fs.existsSync(localVenv)) return localVenv;
  try {
    const commonDir = execFileSync('git', ['-C', repoRoot, 'rev-parse', '--git-common-dir'], { encoding: 'utf8' }).trim();
    if (commonDir) {
      const mainRoot = path.resolve(repoRoot, commonDir, '..');
      const mainVenv = path.join(mainRoot, '.venv', 'bin', 'python');
      if (fs.existsSync(mainVenv)) return mainVenv;
    }
  } catch {
    // Fall through to uv if git metadata is absent.
  }
  return 'uv run python';
}

const py = resolvePythonPrefix();
const webServerEnv = {
  ...process.env,
  PYTHONPATH: process.env.PYTHONPATH ? `src:${process.env.PYTHONPATH}` : 'src',
  CLAIRE_DB_PATH: dbPath,
  CLAIRE_INJECT_PORT: port,
  CLAIRE_PUBLIC_URL: defaultUrl,
  CLAIRE_ALLOW_INSECURE_HTTP: 'true',
  CLAIRE_ENVIRONMENT: 'development',
  CLAIRE_INJECT_TOKEN: 'e2e-owner-token-0123456789abcdef',
  CLAIRE_PRESENTATION_ENABLED: 'true',
};

module.exports = defineConfig({
  testDir: '.',
  testMatch: '**/*.spec.js',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [['line']],
  outputDir: 'test-results',
  use: {
    baseURL,
    browserName: 'chromium',
    headless: true,
    locale: 'ko-KR',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: {
      // Avoid /dev/shm tmpfs exhaustion on low-memory hosts.
      args: ['--disable-dev-shm-usage'],
    },
  },
  webServer: process.env.CLAIRE_E2E_BASE_URL
    ? undefined
    : {
        command: `${py} e2e/seed_db.py && ${py} -m claire.cli serve-api`,
        cwd: '..',
        url: defaultUrl,
        reuseExistingServer: false,
        timeout: 30_000,
        env: webServerEnv,
      },
});
