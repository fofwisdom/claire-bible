const { defineConfig } = require('@playwright/test');

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
    baseURL: process.env.CLAIRE_E2E_BASE_URL || 'http://127.0.0.1:8766/',
    browserName: 'chromium',
    headless: true,
    locale: 'ko-KR',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: {
    command: '/home/fow/.local/bin/uv run python e2e/seed_db.py && /home/fow/.local/bin/uv run python -m claire.cli serve-api',
    cwd: '..',
    url: 'http://127.0.0.1:8766/',
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      ...process.env,
      CLAIRE_DB_PATH: 'data/e2e.db',
      CLAIRE_INJECT_PORT: '8766',
      CLAIRE_PUBLIC_URL: 'http://127.0.0.1:8766/',
      CLAIRE_ALLOW_INSECURE_HTTP: 'true',
      CLAIRE_ENVIRONMENT: 'development',
    },
  },
});
