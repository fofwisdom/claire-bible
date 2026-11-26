// Renditions of a document (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md phases E3b and E3c) against the real E2E server with the mock provider:
// the ingest dialog selects an additional rendition, the Silvercord page shows the section "정리본" with the picker, a choice in the picker opens the page of that rendition, and the owner adds one more rendition from the page.
const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// The errors of the page without the errors of the test tool in a sandbox frame. Playwright runs its own scripts in each frame (the service worker block of test.use, and the trace recorder); a frame with the sandbox policy and no allow-scripts stops them and writes an error. The frame page of a remote rendition has no script of its own.
function pageErrors(errors) {
  return errors.filter((e) => !e.includes('Service worker is disabled because the context is sandboxed') && !(e.includes('Blocked script execution') && e.includes('/renditions/remote/')));
}

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

// A rendition of another Claire server (phase E5c). The E2E server has no other Claire server to read, so the test gives the page the data of one cached copy (the server part is in tests/test_silvercord_rendering.py and tests/test_remote_renditions.py) and checks the page: the picker group, the sandbox frame in the body area, the address, and the way back.
test('a rendition of another server opens in a sandbox frame inside the page and the address keeps the choice', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  const DOC_PATH = '/silvercord/23456789abcdefgh';
  const FRAME_PATH = `${DOC_PATH}/renditions/remote/0123456789abcdef`;
  await page.route((u) => u.pathname === DOC_PATH, async (route) => {
    const res = await route.fetch();
    const text = await res.text();
    const match = text.match(/<script id="docdata" type="application\/json">([\s\S]*?)<\/script>/);
    const doc = JSON.parse(match[1]);
    doc.silvercord.renditions = [{ template: 'standard', version: 'standard-v1', updated_at: 1800000000, primary: true, url: DOC_PATH }];
    doc.silvercord.rendition = 'standard';
    doc.silvercord.remote_renditions = [{ key: '0123456789abcdef', template: 'concise', version: 'concise-v1', updated: '2027-01-15T08:05:20Z', account: '@bob@b.example', domain: 'b.example', origin_url: 'https://b.example/silvercord/tok/renditions/concise', frame_url: FRAME_PATH, opinions_url: `${FRAME_PATH}/opinions` }];
    const json = JSON.stringify(doc).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
    return route.fulfill({ status: res.status(), contentType: 'text/html; charset=utf-8', body: text.replace(match[0], `<script id="docdata" type="application/json">${json}</script>`) });
  });
  await page.route((u) => u.pathname === FRAME_PATH, (route) => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', headers: { 'content-security-policy': "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'; sandbox allow-popups allow-popups-to-escape-sandbox" }, body: '<!DOCTYPE html><html lang="ko"><body><article><h2>핵심</h2><p>다른 서버의 간결 정리본.</p></article></body></html>' }));
  // The 의견 tab follows the shown rendition (Section 5.11 C2): the opinions of the copy come from the route of the copy, and the rendition of the page has its own.
  const opinionAsks = [];
  const opinionsOf = (object, text) => ({ published: true, object, opinions: [{ iri: `${object}/reply`, parent: object, kind: 'reply', local: false, author: { iri: 'https://c.example/users/dora', name: 'Dora', handle: '@dora@c.example' }, url: null, summary: null, sensitive: false, language: 'en', published: 1800000000, text: [[{ text }]], frame_url: null }], counts: { replies: 1, quotes: 0, likes: null, shares: null }, refresh: null, moderation: null, actions: null });
  await page.route((u) => u.pathname === `${FRAME_PATH}/opinions`, (route) => { opinionAsks.push('remote'); return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opinionsOf('https://b.example/ap/objects/9002', 'On the copy')) }); });
  await page.route((u) => u.pathname === `${DOC_PATH}/opinions`, (route) => { opinionAsks.push('local'); return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(opinionsOf('https://claire.example.com/ap/objects/1', 'On the document')) }); });

  await page.goto(DOC_PATH, { waitUntil: 'domcontentloaded' });
  const select = page.locator('#sc-rendition-select');
  await expect(select).toBeVisible();
  await expect(select.locator('optgroup')).toHaveCount(2);
  await expect(select.locator('optgroup').nth(1)).toHaveAttribute('label', '같은 출처 · 다른 서버');
  const remoteOption = select.locator('option[data-group="other-servers"]');
  await expect(remoteOption).toContainText('간결');
  await expect(remoteOption).toContainText('@bob@b.example');
  const localText = await page.locator('#sc-rendition-body').innerText();
  await expectNoRawKeys(page);

  // The choice changes the body area to the frame; the page does not navigate, and the address keeps the choice.
  await select.selectOption('remote:0123456789abcdef');
  const frame = page.locator('#sc-rendition-body iframe.sc-rendition-frame');
  await expect(frame).toBeVisible();
  await expect(frame).toHaveAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
  await expect(frame).toHaveAttribute('title', '@bob@b.example의 간결 정리본');
  await expect(page.frameLocator('#sc-rendition-body iframe.sc-rendition-frame').locator('h2')).toHaveText('핵심');
  expect(new URL(page.url()).search).toBe('?rendition=0123456789abcdef');
  await expect(page.locator('#sc-rendition-info')).toContainText('간결 · concise-v1 · @bob@b.example · 2027-01-15 · b.example');
  await expect(page.locator('#sc-rendition-info a')).toHaveAttribute('href', 'https://b.example/silvercord/tok/renditions/concise');

  // The address with the choice opens the frame at once, and the 의견 tab then shows the opinions of the copy, without the likes and boosts of the origin.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('#sc-rendition-body iframe.sc-rendition-frame')).toBeVisible();
  await expect(page.locator('#sc-rendition-select')).toHaveValue('remote:0123456789abcdef');
  await page.locator('#tab-op-top').click();
  await expect(page.locator('#sc-opinions-inner article')).toContainText('On the copy');
  await expect(page.locator('#sc-opinions-inner .op-counts li')).toHaveText(['답글 1', '인용 0']);
  await page.locator('#tab-doc-top').click();

  // The rendition of the document comes back in the same page.
  await page.locator('#sc-rendition-select').selectOption(DOC_PATH);
  await expect(page.locator('#sc-rendition-body iframe')).toHaveCount(0);
  expect((await page.locator('#sc-rendition-body').innerText()).trim()).toBe(localText.trim());
  expect(new URL(page.url()).search).toBe('');
  // The tab started with the copy; after the change it asks for the opinions of the document.
  await page.locator('#tab-op-top').click();
  await expect(page.locator('#sc-opinions-inner article')).toContainText('On the document');
  expect(opinionAsks).toEqual(['remote', 'local']);
  expect(pageErrors(errors)).toEqual([]);
});

// The +1 control (phase E9b). The page data comes from the test, as in the test of the frame; the route and the counts of the server are in tests/test_plus_one_marks.py.
test('the +1 control is a toggle for a reader, follows the rendition that the page shows, and is text for the author', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  const DOC_PATH = '/silvercord/23456789abcdefgh';
  const FRAME_PATH = `${DOC_PATH}/renditions/remote/0123456789abcdef`;
  let own = false;
  const sent = [];
  await page.route((u) => u.pathname === DOC_PATH, async (route) => {
    const res = await route.fetch();
    const text = await res.text();
    const match = text.match(/<script id="docdata" type="application\/json">([\s\S]*?)<\/script>/);
    const doc = JSON.parse(match[1]);
    doc.silvercord.renditions = [{ template: 'standard', version: 'standard-v1', updated_at: 1800000000, primary: true, url: DOC_PATH, plus: { score: 2, mine: false, own } }];
    doc.silvercord.rendition = 'standard';
    doc.silvercord.remote_renditions = [{ key: '0123456789abcdef', template: 'concise', version: 'concise-v1', updated: '2027-01-15T08:05:20Z', account: '@bob@b.example', domain: 'b.example', origin_url: 'https://b.example/x', frame_url: FRAME_PATH, plus: { score: 7, mine: true, own: false } }];
    doc.silvercord.plus_one = { url: '/api/renditions/plus-one', sign_in: null };
    const json = JSON.stringify(doc).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
    return route.fulfill({ status: res.status(), contentType: 'text/html; charset=utf-8', body: text.replace(match[0], `<script id="docdata" type="application/json">${json}</script>`) });
  });
  await page.route((u) => u.pathname === FRAME_PATH, (route) => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: '<!DOCTYPE html><html><body><p>remote</p></body></html>' }));
  await page.route((u) => u.pathname === '/api/renditions/plus-one', (route) => {
    const body = JSON.parse(route.request().postData() || '{}');
    sent.push(body);
    const base = body.rendition === 'standard' ? 2 : 6;
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ on: body.on, score: base + (body.on ? 1 : 0) }) });
  });

  await page.goto(DOC_PATH, { waitUntil: 'domcontentloaded' });
  const button = page.locator('#sc-plus-one');
  await expect(button).toBeVisible();
  await expect(button).toHaveAttribute('aria-pressed', 'false');
  await expect(button).toHaveAttribute('aria-label', '이 정리본에 +1');
  await expect(page.locator('#sc-plus-one-count')).toHaveText('2');
  await expect(page.locator('#sc-rendition-select option[data-template="standard"]')).toContainText('+1 2');
  await expectNoRawKeys(page);

  // The toggle gives the +1 and takes it back; the count is the score of the answer.
  await button.click();
  await expect(page.locator('#sc-plus-one')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#sc-plus-one-count')).toHaveText('3');
  await page.locator('#sc-plus-one').click();
  await expect(page.locator('#sc-plus-one')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#sc-plus-one-count')).toHaveText('2');

  // The control follows the rendition of the body area: the rendition of the other server has its own mark and count.
  await page.locator('#sc-rendition-select').selectOption('remote:0123456789abcdef');
  await expect(page.locator('#sc-plus-one')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('#sc-plus-one-count')).toHaveText('7');
  await page.locator('#sc-plus-one').click();
  await expect(page.locator('#sc-plus-one')).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#sc-plus-one-count')).toHaveText('6');
  expect(sent).toEqual([
    { token: '23456789abcdefgh', rendition: 'standard', on: true },
    { token: '23456789abcdefgh', rendition: 'standard', on: false },
    { token: '23456789abcdefgh', rendition: 'remote:0123456789abcdef', on: false },
  ]);

  // The author sees the count as text, with no control.
  own = true;
  await page.goto(DOC_PATH, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#sc-plus-one-count')).toHaveText('2');
  await expect(page.locator('#sc-plus-one')).toHaveCount(0);
  expect(pageErrors(errors)).toEqual([]);
});
