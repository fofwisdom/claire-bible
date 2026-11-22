const crypto = require('node:crypto');
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('OAuth consent page lets the user clear the write scope and issues a readonly token', async ({ page, baseURL }) => {
  const origin = new URL(baseURL).origin;
  const errors = watchErrors(page);
  await authenticateOwner(page, 'oauthwrite1');

  const redirectUri = `${origin}/oauth-e2e-callback`;
  const reg = await page.request.post('/oauth/register', { data: { client_name: 'E2E chat connector', redirect_uris: [redirectUri] } });
  expect(reg.status()).toBe(201);
  const clientId = (await reg.json()).client_id;

  const verifier = crypto.randomBytes(40).toString('base64url');
  const challenge = crypto.createHash('sha256').update(verifier).digest('base64url');
  const query = new URLSearchParams({ response_type: 'code', client_id: clientId, redirect_uri: redirectUri, state: 'e2e-state', code_challenge: challenge, code_challenge_method: 'S256', scope: 'readonly write' });
  await page.goto(`/oauth/authorize?${query.toString()}`, { waitUntil: 'domcontentloaded' });

  const writeBox = page.locator('input[name="grant_write"]');
  await expect(writeBox).toBeChecked();
  await expect(page.locator('label.choice')).toContainText(/Allow write access|쓰기 권한 허용/);
  await writeBox.uncheck();

  await Promise.all([
    page.waitForURL((url) => url.pathname === '/oauth-e2e-callback' && url.searchParams.has('code')),
    page.locator('button.primary').click(),
  ]);
  const callback = new URL(page.url());
  expect(callback.searchParams.get('state')).toBe('e2e-state');

  const token = await page.request.post('/oauth/token', {
    form: { grant_type: 'authorization_code', code: callback.searchParams.get('code'), client_id: clientId, redirect_uri: redirectUri, code_verifier: verifier },
  });
  expect(token.status()).toBe(200);
  expect((await token.json()).scope).toBe('readonly');
  expect(errors.filter((line) => !line.includes('oauth-e2e-callback'))).toEqual([]);
});
