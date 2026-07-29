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
});
