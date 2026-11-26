// The extraction choice of the ingest dialog (AGENT_DRIVEN_EXTRACTION_AND_EMBEDDING_DESIGN.md decision AED4-rev) against the real E2E server: with an API, "Ingest now with the API" is first and checked, and "Wait for the agent client" is second; without an API, the queue of the agent client is the only choice. Each ingest request sends the choice in "extraction_mode".
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

async function openIngestDialog(page) {
  await page.locator('#addbtn').click();
  await page.locator('#pop-ingest-menu [data-ingest-cmd="ingest"]').click();
  const modal = page.locator('#modal-ingest');
  await expect(modal).toBeVisible();
  return modal;
}

function extractionValues(modal) {
  return modal.locator('#ingest-extraction-grid input[name="ingest-extraction"]').evaluateAll((els) => els.map((el) => el.value));
}

test('with an API the dialog shows the API first and checked, then the agent client queue, and sends each choice', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  const bodies = [];
  page.on('request', (req) => {
    if (req.method() === 'POST' && new URL(req.url()).pathname.endsWith('/ingest-stream')) bodies.push(JSON.parse(req.postData() || '{}'));
  });
  await authenticateOwner(page, 'ingestmode1');

  // The administrator has the server providers of the E2E server (mock): two choices, the API first and checked.
  let modal = await openIngestDialog(page);
  await expect.poll(() => extractionValues(modal)).toEqual(['provider', 'client']);
  await expect(modal.locator('#ingextraction-provider')).toBeChecked();
  await expect(modal.locator('label[for="ingextraction-provider"]')).toHaveText('API로 바로 적재');
  await expect(modal.locator('label[for="ingextraction-client"]')).toHaveText('에이전트 클라이언트 대기');
  await expect(modal.locator('#ingest-extraction-hint')).toBeHidden();
  const apiOnly = modal.locator('fieldset[data-api-only]');
  await expect(apiOnly).toHaveCount(2);
  await expectNoRawKeys(page);

  // The queue of the agent client: the amount and the effort do not apply. The server keeps the source in the queue.
  await modal.locator('label[for="ingextraction-client"]').click();
  // Playwright's toBeDisabled ignores a fieldset: the fieldset has the attribute, and its inputs are disabled.
  for (const fieldset of await apiOnly.all()) await expect(fieldset).toHaveAttribute('disabled', '');
  await expect(modal.locator('#ingamount-full')).toBeDisabled();
  await modal.locator('#ingin').fill('Title: Queue note\nBody: a note that waits for the agent client of the owner.');
  await modal.locator('#btn-submit-ingest').click();
  const staged = page.locator('#panel #ingest-staged');
  await expect(staged).toBeVisible();
  await expect(staged).toContainText('에이전트 클라이언트 대기 중');
  await expect(page.locator('#panel')).toContainText('원문 전체가 잡 큐에서 기다립니다.');
  expect(bodies).toHaveLength(1);
  expect(bodies[0]).toMatchObject({ extraction_mode: 'client', full_content: false, renditions: [] });
  expect(bodies[0].effort).toBeUndefined();

  // A new dialog checks the API again (the default), and the API extracts at once.
  modal = await openIngestDialog(page);
  await expect(modal.locator('#ingextraction-provider')).toBeChecked();
  for (const fieldset of await apiOnly.all()) await expect(fieldset).not.toHaveAttribute('disabled', /.*/);
  await expect(modal.locator('#ingamount-full')).toBeEnabled();
  await modal.locator('#ingin').fill('Title: API note\nBody: a note that the API of the server extracts at once.');
  await modal.locator('#btn-submit-ingest').click();
  await expect(page.locator('#panel h2')).toContainText('적재 완료');
  await expect(page.locator('#panel #ingest-staged')).toHaveCount(0);
  expect(bodies).toHaveLength(2);
  expect(bodies[1].extraction_mode).toBe('provider');

  expect(errors).toEqual([]);
});

test('without an API the dialog has only the agent client queue and tells how to register a key', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  // An account without an API: GET stats gives only the queue (provider_policy: a member without a key and without a server tier).
  await page.route((u) => u.pathname.endsWith('/stats'), async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.extraction_modes = ['client'];
    await route.fulfill({ response, json: body });
  });
  await authenticateOwner(page, 'ingestmode2');

  const modal = await openIngestDialog(page);
  await expect.poll(() => extractionValues(modal)).toEqual(['client']);
  await expect(modal.locator('#ingextraction-client')).toBeChecked();
  await expect(modal.locator('#ingextraction-provider')).toHaveCount(0);
  await expect(modal.locator('#ingest-extraction-hint')).toBeVisible();
  await expect(modal.locator('#ingest-extraction-hint')).toContainText('등록된 API가 없어 에이전트 클라이언트를 기다립니다.');
  for (const fieldset of await modal.locator('fieldset[data-api-only]').all()) await expect(fieldset).toHaveAttribute('disabled', '');
  await expect(modal.locator('#ingamount-full')).toBeDisabled();
  await expectNoRawKeys(page);

  expect(errors).toEqual([]);
});
