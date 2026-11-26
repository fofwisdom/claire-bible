// Renditions of a document (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md phases E3b and E3c) against the real E2E server with the mock provider:
// the ingest dialog selects an additional rendition, the Silvercord page shows the section "정리본" with the picker, a choice in the picker opens the page of that rendition, and the owner adds one more rendition from the page.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

test('an ingest with 간결 makes two renditions, the picker opens each page, and the owner adds 학술', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'renditions1');

  // The ingest dialog: 기본 is always made; 간결 and 학술 are choices, and the request sends the checked ones.
  await page.locator('#addbtn').click();
  await page.locator('#pop-ingest-menu [data-ingest-cmd="ingest"]').click();
  const modal = page.locator('#modal-ingest');
  await expect(modal).toBeVisible();
  await expect(modal.locator('#ingrendition-standard')).toBeChecked();
  await expect(modal.locator('#ingrendition-standard')).toBeDisabled();
  await expect(modal.locator('label[for="ingrendition-concise"]')).toHaveText('간결');
  await expect(modal.locator('#ingrendition-concise')).not.toBeChecked();
  await expectNoRawKeys(page);
  await modal.locator('label[for="ingrendition-concise"]').click();
  await modal.locator('#ingin').fill('Title: Rendition note\nBody: a note about the rendition templates of Claire Bible, written for the rendition test of the E2E server.');
  const sent = page.waitForRequest((req) => req.method() === 'POST' && new URL(req.url()).pathname.endsWith('/ingest-stream'));
  await modal.locator('#btn-submit-ingest').click();
  expect(JSON.parse((await sent).postData() || '{}')).toMatchObject({ extraction_mode: 'provider', renditions: ['concise'] });
  await expect(page.locator('#panel h2')).toContainText('적재 완료');

  // The Silvercord page of the new document: the section "정리본" starts with the picker, and 기본 (the 대표 정리본) is selected.
  // The page reads the progress stream itself, so the test finds the document through the document list.
  const permalink = await page.evaluate(async () => {
    // The list starts with the newest document: the seeded documents are older.
    const list = await (await fetch('/api/v1/kbs/personal/documents?limit=5', { credentials: 'same-origin', headers: { Accept: 'application/json' } })).json();
    const doc = list.items[0];
    const detail = await (await fetch('/api/v1/kbs/personal/documents/' + encodeURIComponent(doc.id), { credentials: 'same-origin', headers: { Accept: 'application/json' } })).json();
    return { permalink: detail.permalink, title: detail.title };
  }).then((found) => { expect(found.title).toContain('Rendition'); return found.permalink; });
  const docPath = new URL(permalink, page.url()).pathname;
  expect(docPath).toMatch(/^\/silvercord\/[0-9a-z]+$/);
  await page.goto(docPath, { waitUntil: 'domcontentloaded' });
  const select = page.locator('#sc-rendition-select');
  await expect(select).toBeVisible();
  await expect(page.locator('label[for="sc-rendition-select"]')).toHaveText('정리본');
  await expect(select.locator('option')).toHaveCount(2);
  await expect(select.locator('option:checked')).toHaveAttribute('data-template', 'standard');
  await expect(select.locator('option[data-template="standard"]')).toContainText('대표 정리본');
  await expect(page.locator('#sc-rendition-info')).toContainText('기본 · standard-v1');
  await expect(page.locator('#wrap .doc-content').last()).toContainText('[template: standard]');
  await expectNoRawKeys(page);

  // A choice in the picker opens the page of that rendition (a navigation, Section 5.10 rule 3).
  await Promise.all([page.waitForURL(`**${docPath}/renditions/concise`), select.selectOption(`${docPath}/renditions/concise`)]);
  await expect(page.locator('#sc-rendition-select option:checked')).toHaveAttribute('data-template', 'concise');
  await expect(page.locator('#sc-rendition-info')).toContainText('간결 · concise-v1');
  await expect(page.locator('#wrap .doc-content').last()).toContainText('[template: concise]');

  // 정리본 추가: the owner gets the templates that the document does not have; the new rendition opens when it is made.
  await page.locator('#sc-rendition-add-btn').click();
  const addPopover = page.locator('#sc-rendition-add');
  await expect(addPopover).toBeVisible();
  await expect(addPopover.locator('[data-add-rendition]')).toHaveCount(1);
  await Promise.all([page.waitForURL(`**${docPath}/renditions/academic`), addPopover.locator('[data-add-rendition="academic"]').click()]);
  await expect(page.locator('#sc-rendition-select option')).toHaveCount(3);
  await expect(page.locator('#wrap .doc-content').last()).toContainText('[template: academic]');
  await expect(page.locator('#sc-rendition-add-btn')).toHaveCount(0);

  // The address of the 대표 정리본 is the address of the document.
  await page.goto(`${docPath}/renditions/standard`, { waitUntil: 'domcontentloaded' });
  expect(new URL(page.url()).pathname).toBe(docPath);

  expect(errors).toEqual([]);
});
