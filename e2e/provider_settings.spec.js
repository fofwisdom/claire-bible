// The AI provider pages (PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md) against the real E2E server: /admin/providers shows what applies now and one form for each part, each result next to its own buttons; STT has no provider choice; /account/providers shows what applies to the jobs of the account and one card for the key.
// The E2E server keeps data/providers.json of the repository: each test restores the values that it saves.
const { test, expect } = require('@playwright/test');
const { authenticateOwner } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// The result of a form is next to its buttons: in the same row, at most one row height away.
async function expectStatusNextToButton(form, button, text) {
  const status = form.locator('[data-prov-status], [data-settings-result]').first();
  await expect(status).toContainText(text);
  const s = await status.boundingBox();
  const b = await button.boundingBox();
  expect(Math.abs(s.y - b.y)).toBeLessThan(40);
  await expect(status).toBeInViewport();
}

test('admin providers: what applies now, fixed STT, and a result next to the button of each form', async ({ page }) => {
  test.setTimeout(60_000);
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.goto('/?t=e2e-routing-token-0123456789abcde', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });

  const effective = page.locator('#prov-effective-card');
  await expect(effective).toBeVisible();
  await expect(effective.locator('[data-prov-effective="stt"]')).toContainText('gemini-3.5-transcribe');
  await expect(effective.locator('[data-prov-effective="stt"]')).toContainText('고정');
  await expect(effective.locator('[data-prov-effective="members"] a[href="/admin/server#server-tier"]')).toBeVisible();
  // Removed: the lineage diagram, the routing rows of STT and VLM, the STT provider choice, and the server tier card (it is on /admin/server).
  for (const gone of ['#prov-lineage-svg', '#prov-lineage-visualizer-card', '#prov-member-tier-card', '#prov-route-stt-model', '#prov-route-vlm-model', '#prov-stt-prov', '#prov-save-btn']) {
    await expect(page.locator(gone)).toHaveCount(0);
  }
  await expect(page.locator('#prov-stt-fixed')).toContainText('바꿀 수 없습니다');

  // STT: save a language; the result is next to the button and the status card shows the new value. Then restore it.
  const sttForm = page.locator('#prov-stt-form');
  const sttLang = page.locator('#prov-stt-lang');
  const originalLang = await sttLang.inputValue();
  await sttLang.fill('en');
  const sttSave = sttForm.locator('button[type="submit"]');
  await sttSave.click();
  await expectStatusNextToButton(sttForm, sttSave, '저장했습니다');
  await expect(effective.locator('[data-prov-effective="stt"]')).toContainText('en');
  await sttLang.fill(originalLang);
  await sttSave.click();
  await expectStatusNextToButton(sttForm, sttSave, '저장했습니다');

  // Models: a model for one feature (advanced) shows in the status card. Then restore it.
  const modelForm = page.locator('#prov-model-form');
  await modelForm.locator('#prov-routing summary').click();
  const summarize = page.locator('#prov-route-summarize-model');
  const originalSummarize = await summarize.inputValue();
  const optionValues = await summarize.locator('option').evaluateAll(els => els.map(el => el.value).filter(Boolean));
  await summarize.selectOption(optionValues[0]);
  const modelSave = modelForm.locator('button[type="submit"]');
  await modelSave.click();
  await expectStatusNextToButton(modelForm, modelSave, '저장했습니다');
  await expect(page.locator('#prov-effective-card [data-prov-effective="text"]')).toContainText(optionValues[0]);
  await summarize.selectOption(originalSummarize);
  await modelSave.click();
  await expectStatusNextToButton(modelForm, modelSave, '저장했습니다');

  // Key: a save without a key tells it next to the button and moves the focus to the field.
  const keyForm = page.locator('#prov-key-form');
  const keySave = keyForm.locator('button[value="save"]');
  await keySave.click();
  await expectStatusNextToButton(keyForm, keySave, '먼저 키를 넣으세요');
  await expect(page.locator('#prov-gemini-key')).toBeFocused();

  // Advanced (development server): the test provider switch exists. Its change to Google AI Studio without a key fails next to the button, and the switch keeps the value that the person set. With a key the save would change the main provider: the test does not save then.
  const advForm = page.locator('#prov-advanced-form');
  await advForm.locator('#prov-advanced summary').click();
  const mock = page.locator('#prov-mock-enabled');
  await expect(mock).toBeVisible();
  const noKey = await page.locator('#prov-key-form [data-prov-key-state="unset"]').count() === 1;
  if (noKey && await mock.isChecked()) {
    await mock.uncheck();
    const advSave = advForm.locator('button[type="submit"]');
    await advSave.click();
    await expectStatusNextToButton(advForm, advSave, '저장 실패');
    await expect(mock).not.toBeChecked();
  }

  expect(pageErrors).toEqual([]);
});

test('admin providers on a production server: no test provider switch, and the problems in the status card', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  await authenticateOwner(page, 'provsettings1');
  // The answer of GET /providers of a production server with Google AI Studio and no key.
  await page.route(u => u.pathname === '/providers', async (route) => {
    if (route.request().method() !== 'GET') return route.continue();
    const res = await route.fetch();
    const body = await res.json();
    body.active_provider = 'gemini';
    body.providers.gemini.has_api_key = false;
    body.effective = { ...body.effective, is_production: true, main_provider: 'gemini', effective_provider: 'unavailable', api_key_set: false, problems: ['no_api_key', 'stt_not_ready'] };
    await route.fulfill({ response: res, json: body });
  });
  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#prov-problems')).toContainText('API 키가 없어 AI 작업을 하지 않습니다');
  await expect(page.locator('#prov-effective-card [data-prov-effective="text"]')).toContainText('동작 안 함');
  await expect(page.locator('#prov-key-form [data-prov-key-state="unset"]')).toBeVisible();
  await page.locator('#prov-advanced summary').click();
  await expect(page.locator('#prov-mock-enabled')).toHaveCount(0);
});

test('account providers: what applies to my jobs and one key card with the result next to its buttons', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  await authenticateOwner(page, 'provsettings2');
  await page.goto('/account/providers', { waitUntil: 'domcontentloaded' });
  const effective = page.locator('#settings-effective');
  await expect(effective).toBeVisible();
  // The administrator without a personal key uses the server providers; no server tier limit applies.
  await expect(effective).toHaveAttribute('data-effective-source', 'server');
  await expect(effective.locator('[data-effective-row="stt"], [data-effective-row="ingest"]').first()).toBeVisible();
  await expect(effective.locator('[data-effective-limits="exempt"]')).toBeVisible();
  const keyCard = page.locator('#settings-keys');
  await expect(keyCard.locator('[data-key-state="none"]')).toBeVisible();
  await expect(keyCard.locator('a[href="/admin/providers"]')).toBeVisible();
  // Old separate cards are gone: one card has the actions.
  for (const gone of ['#settings-keys-put', '#settings-keys-verify', '#settings-keys-delete', '#settings-keys-meta']) {
    await expect(page.locator(gone)).toHaveCount(0);
  }
  const save = keyCard.locator('button[value="save"]');
  await save.click();
  await expectStatusNextToButton(keyCard, save, '먼저 API 키를 넣으세요');
});
