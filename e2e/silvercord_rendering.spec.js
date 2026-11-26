const { test, expect } = require('@playwright/test');
const { watchErrors, expectNoRawKeys } = require('./support/console_pages');

// The PWA service worker intercepts fetch requests outside page.route(); block it like sibling specs.
test.use({ serviceWorkers: 'block' });

const PUBLIC_DOC_TOKEN = '23456789abcdefgh';
const PUBLIC_DOC_PATH = `/silvercord/${PUBLIC_DOC_TOKEN}`;
const PUBLIC_DOC_WS_PATH = `${PUBLIC_DOC_PATH}/workspace`;
const PUBLIC_DOC_GRAPH_PATH = `${PUBLIC_DOC_PATH}/graph`;
const LINK_ONLY_TOKEN = '99999999linkonly';
const LINK_ONLY_PATH = `/silvercord/${LINK_ONLY_TOKEN}`;
const LINK_ONLY_GRAPH_PATH = `${LINK_ONLY_PATH}/graph`;
const OWNER_COOKIE_TOKEN = 'e2e-owner-token-0123456789abcdef';

// Emoji detection pattern matching tests/test_ui_line_icons.py (zero emojis in Silvercord chrome and the 의견 tab).
const EMOJI_RE = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u;

function escapeDocDataJson(obj) {
  return JSON.stringify(obj).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
}

function patchDocDataInHtml(html, mutator) {
  const match = html.match(/<script id="docdata" type="application\/json">([\s\S]*?)<\/script>/);
  if (!match) {
    throw new Error('Missing #docdata script in Silvercord HTML');
  }
  const doc = JSON.parse(match[1]);
  mutator(doc);
  return html.replace(match[0], `<script id="docdata" type="application/json">${escapeDocDataJson(doc)}</script>`);
}

async function readDocData(page) {
  return page.evaluate(() => {
    const el = document.getElementById('docdata');
    return el ? JSON.parse(el.textContent || '{}') : null;
  });
}

test('public KB document shows unified Silvercord view for anonymous and signed-in member with workspace switch', async ({ browser, baseURL }) => {
  const targetOrigin = new URL(baseURL || 'http://127.0.0.1:8766/').origin;

  // 1. Anonymous visitor on real E2E server.
  const anonContext = await browser.newContext({ viewport: { width: 1280, height: 800 }, serviceWorkers: 'block' });
  const anonPage = await anonContext.newPage();
  const anonErrors = watchErrors(anonPage);

  await anonPage.goto(PUBLIC_DOC_PATH, { waitUntil: 'domcontentloaded' });
  await expect(anonPage.locator('#sc-bar')).toBeVisible();
  await expect(anonPage.locator('#sc-kb-badge')).toHaveText('기본 지식베이스');
  await expect(anonPage.locator('#sc-panel-doc')).toBeVisible();
  await expect(anonPage.locator('#sc-panel-opinions')).toHaveAttribute('hidden', 'until-found');
  await expect(anonPage.locator('body')).not.toHaveClass(/sc-graph-open/);
  await expect(anonPage.locator('#sc-desktop-aside')).toBeHidden();
  await expect(anonPage.locator('#sc-graph-toggle-top')).toHaveAttribute('aria-expanded', 'false');
  await expect(anonPage.locator('#sc-graph-count-top')).toHaveText('2');
  await expect(anonPage.locator('#sc-ws-link')).toBeVisible();
  await expect(anonPage.locator('#sc-ws-link')).toHaveAttribute('href', PUBLIC_DOC_WS_PATH);

  const anonDocData = await readDocData(anonPage);
  expect(anonDocData.nodes).toBeUndefined();
  expect(anonDocData.silvercord.can_open_workspace).toBe(true);
  expect(anonDocData.silvercord.link_only).toBe(false);
  expect(anonDocData.silvercord.workspace_url).toBe(PUBLIC_DOC_WS_PATH);
  await expectNoRawKeys(anonPage);

  // Anonymous visitor can also open /silvercord/{token}/workspace directly in Workspace.
  await anonPage.locator('#sc-ws-link').click();
  await anonPage.waitForURL(`**${PUBLIC_DOC_WS_PATH}`);
  await expect(anonPage.locator('meta[name="claire-open-item"]')).toHaveAttribute('content', 'document:doc-1');
  await expect(anonPage.locator('#reader')).toBeVisible();
  await expect(anonPage.locator('#reader')).toContainText('테스트 문서 1 (핵심 개념)');
  expect(anonErrors).toEqual([]);
  await anonContext.close();

  // 2. Signed-in KB member on real E2E server (SQ1:B, SQ2:B+A1).
  const memberContext = await browser.newContext({ viewport: { width: 1280, height: 800 }, serviceWorkers: 'block' });
  await memberContext.addCookies([{ name: 'claire_session', value: OWNER_COOKIE_TOKEN, url: targetOrigin }]);
  const memberPage = await memberContext.newPage();
  const memberErrors = watchErrors(memberPage);

  await memberPage.goto(PUBLIC_DOC_PATH, { waitUntil: 'domcontentloaded' });
  await expect(memberPage.locator('#sc-bar')).toBeVisible();
  await expect(memberPage.locator('#sc-kb-badge')).toHaveText('기본 지식베이스');
  await expect(memberPage.locator('#sc-panel-doc')).toBeVisible();
  await expect(memberPage.locator('body')).not.toHaveClass(/sc-graph-open/);
  await expect(memberPage.locator('#sc-graph-toggle-top')).toHaveAttribute('aria-expanded', 'false');
  await expect(memberPage.locator('#sc-ws-link')).toBeVisible();
  await expect(memberPage.locator('#sc-ws-link')).toHaveAttribute('href', PUBLIC_DOC_WS_PATH);

  // Lazy graph fetch against real /silvercord/{token}/graph works for signed-in member too.
  await memberPage.locator('#sc-graph-toggle-top').click();
  await expect(memberPage.locator('body')).toHaveClass(/sc-graph-open/);
  await expect(memberPage.locator('#sc-desktop-aside svg.ctx-local-graph .ctx-lg-node.ring-1')).toHaveCount(2);

  // Opening workspace link lands in Workspace as owner with doc-1 open.
  await memberPage.locator('#sc-ws-link').click();
  await memberPage.waitForURL(`**${PUBLIC_DOC_WS_PATH}`);
  await expect(memberPage.locator('meta[name="claire-open-item"]')).toHaveAttribute('content', 'document:doc-1');
  await expect.poll(() => memberPage.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await expect(memberPage.locator('#reader')).toBeVisible();
  await expect(memberPage.locator('#reader')).toContainText('테스트 문서 1 (핵심 개념)');
  expect(memberErrors).toEqual([]);
  await memberContext.close();
});

test('desktop pull-out graph panel lazily fetches Ring 1 + Ring 2, highlights body marks on hover, and drills down with back button', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1280, height: 800 });

  let graphFetchCount = 0;
  await page.route((u) => u.pathname === PUBLIC_DOC_PATH, async (route) => {
    const req = route.request();
    if (req.resourceType() !== 'document') return route.fallback();
    const res = await route.fetch();
    const html = patchDocDataInHtml(await res.text(), (doc) => {
      doc.detail_html = `<p>본문에서 엔티티 A와 엔티티 B가 연결되는 구조를 설명합니다.</p>${doc.detail_html || ''}`;
    });
    return route.fulfill({ status: res.status(), contentType: 'text/html; charset=utf-8', body: html });
  });

  await page.route((u) => u.pathname === PUBLIC_DOC_GRAPH_PATH, async (route) => {
    graphFetchCount += 1;
    const res = await route.fetch();
    const payload = await res.json();
    expect(payload.scope).toBe('full');
    expect(payload.ring1).toHaveLength(2);
    payload.ring2 = [
      { id: 'ent-3', label: '엔티티 C', group: 'Model', degree: 1, aliases: [], observations: ['이웃 모델 설명'], entity_path: '/entity/78923456abcdefgh', other_docs: [{ title: '테스트 문서 2 (응용 도구)', path: '/silvercord/45678923abcdefgh' }], hidden_other_docs_count: 0, connected_r1: ['ent-1'] },
    ];
    payload.edges.push({ from: 'ent-1', to: 'ent-3', rel: 'depends_on', provisional: true });
    return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(payload) });
  });

  await page.goto(PUBLIC_DOC_PATH, { waitUntil: 'domcontentloaded' });
  expect(graphFetchCount).toBe(0);
  await expect(page.locator('body')).not.toHaveClass(/sc-graph-open/);
  await expect(page.locator('#sc-desktop-aside')).toBeHidden();

  // Clicking graph toggle lazily fetches /graph and opens the 360px right column.
  await page.locator('#sc-graph-toggle-top').click();
  await expect.poll(() => graphFetchCount).toBe(1);
  await expect(page.locator('body')).toHaveClass(/sc-graph-open/);
  await expect(page.locator('#sc-graph-toggle-top')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#sc-desktop-aside')).toBeVisible();

  const asideBox = await page.locator('#sc-desktop-aside').boundingBox();
  expect(asideBox).not.toBeNull();
  expect(Math.abs(asideBox.width - 360)).toBeLessThanOrEqual(2);

  // SVG renders Ring 1 (2 nodes) + Ring 2 (1 node) and edges.
  const svg = page.locator('#sc-desktop-aside svg.ctx-local-graph');
  await expect(svg).toBeVisible();
  await expect(svg.locator('.ctx-lg-node.ring-1')).toHaveCount(2);
  await expect(svg.locator('.ctx-lg-node.ring-2')).toHaveCount(1);
  await expect(svg.locator('.ctx-lg-edge')).toHaveCount(2);

  // CSS Custom Highlight API marks are installed on the document body.
  await expect.poll(() => page.evaluate(() => (CSS.highlights && CSS.highlights.get('claire-entity') ? CSS.highlights.get('claire-entity').size : 0))).toBeGreaterThan(0);

  // Hovering a Ring 1 node highlights both the SVG node and the body entity mark.
  const nodeA = svg.locator('.ctx-lg-node[data-entity-id="ent-1"]');
  await nodeA.hover();
  await expect(nodeA).toHaveClass(/hover-active/);
  await expect.poll(() => page.evaluate(() => (CSS.highlights && CSS.highlights.get('claire-entity-hover') ? CSS.highlights.get('claire-entity-hover').size : 0))).toBeGreaterThan(0);

  await page.locator('#sc-bar').hover();
  await expect.poll(() => page.evaluate(() => (CSS.highlights && CSS.highlights.get('claire-entity-hover') ? CSS.highlights.get('claire-entity-hover').size : 0))).toBe(0);

  // Clicking a node drills down into Entity Detail with a back button (SQ5:A).
  await nodeA.click();
  const detailCard = page.locator('#sc-desktop-aside .gp-detail-card');
  await expect(detailCard).toBeVisible();
  await expect(detailCard.locator('h3')).toContainText('엔티티 A');
  await expect(detailCard.locator('.gp-detail-id code')).toHaveText('ent-1');
  await expect(page.locator('#gp-back-btn')).toBeVisible();

  await page.locator('#gp-back-btn').click();
  await expect(detailCard).toHaveCount(0);
  await expect(svg).toBeVisible();

  // Re-opening graph panel reuses cached data without a second fetch.
  await page.locator('#sc-desktop-aside .gp-close-btn').click();
  await expect(page.locator('body')).not.toHaveClass(/sc-graph-open/);
  await page.locator('#sc-graph-toggle-top').click();
  await expect(svg).toBeVisible();
  expect(graphFetchCount).toBe(1);
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('mobile bottom bar switches doc and opinions preserving scrollY, syncs srail, opens modal dialog sheet, and renders an emoji-free 의견 tab', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });

  await page.goto(PUBLIC_DOC_PATH, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#sc-bottom-bar')).toBeVisible();
  await expect(page.locator('#sc-tablist-bottom')).toHaveAttribute('role', 'tablist');
  await expect(page.locator('#tab-doc-bot')).toHaveAttribute('role', 'tab');
  await expect(page.locator('#tab-op-bot')).toHaveAttribute('role', 'tab');
  // Graph toggle button is outside role="tablist" per SQ3:B.
  await expect(page.locator('#sc-graph-toggle-bot')).toBeVisible();
  await expect(page.locator('#sc-tablist-bottom #sc-graph-toggle-bot')).toHaveCount(0);

  // Scroll down on doc tab and verify #srail is visible.
  await page.evaluate(() => window.scrollTo({ top: 640, behavior: 'instant' }));
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(400);
  const savedY = await page.evaluate(() => window.scrollY);
  await expect(page.locator('#srail')).toBeVisible();

  // Switch to 의견 tab: #srail hides and the tab loads its JSON (the document of the seed is not published).
  await page.locator('#tab-op-bot').click();
  await expect(page.locator('#tab-op-bot')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#tab-doc-bot')).toHaveAttribute('aria-selected', 'false');
  await expect(page.locator('#sc-panel-opinions')).toBeVisible();
  await expect(page.locator('#sc-panel-doc')).toHaveAttribute('hidden', 'until-found');
  await expect(page.locator('#srail')).toBeHidden();

  await expect(page.locator('#sc-opinions-inner .op-root[data-nosnippet]')).toBeVisible();
  await expect(page.locator('#sc-opinions-inner progress')).toHaveCount(0);

  const uiTexts = await page.evaluate(() => {
    const parts = [];
    ['#sc-bar', '#sc-bottom-bar', '#sc-panel-opinions'].forEach((sel) => {
      const el = document.querySelector(sel);
      if (el) parts.push(el.innerText || '');
    });
    return parts.join('\n');
  });
  expect(EMOJI_RE.test(uiTexts)).toBe(false);

  // Switch back to 본문 tab: scrollY is restored and #srail returns.
  await page.locator('#tab-doc-bot').click();
  await expect(page.locator('#sc-panel-doc')).toBeVisible();
  await expect(page.locator('#srail')).toBeVisible();
  await expect.poll(() => page.evaluate((y) => Math.abs(window.scrollY - y), savedY)).toBeLessThanOrEqual(5);

  // Clicking graph toggle opens native modal <dialog> bottom sheet and hides #srail.
  await page.locator('#sc-graph-toggle-bot').click();
  const dialog = page.locator('#sc-mobile-dialog');
  await expect(dialog).toBeVisible();
  await expect(dialog).toHaveAttribute('open', '');
  expect(await dialog.evaluate((el) => el.matches(':modal'))).toBe(true);
  await expect(page.locator('#srail')).toBeHidden();
  await expect(dialog.locator('svg.ctx-local-graph .ctx-lg-node.ring-1')).toHaveCount(2);

  await dialog.locator('.gp-close-btn').click();
  await expect(dialog).toBeHidden();
  await expect(page.locator('#srail')).toBeVisible();
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

// The JSON of the 의견 tab (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.11 D, phase E7b). A post in the normal form of the route: the text is paragraphs of segments.
function opinion(iri, parent, extra = {}) {
  return { iri, parent, kind: 'reply', local: false, author: { iri: 'https://remote.example/users/carol', name: 'Carol', handle: '@carol@remote.example' }, url: iri, summary: null, sensitive: false, language: 'en', published: 1800000000 + Number(iri.split('/').pop()), text: [[{ text: 'Reply ' + iri.split('/').pop() }]], ...extra };
}

test('the 의견 tab loads on the first open, waits for the fetch with a progress bar, and shows the tree, the quotes, and the new opinions once', async ({ page }) => {
  const errors = watchErrors(page);
  const object = 'https://claire.example.com/ap/objects/1';
  const posts = [
    opinion('https://remote.example/notes/1', object, { text: [[{ text: 'Read ' }, { text: 'the source', href: 'https://example.org/paper' }, { text: '\n' }, { text: '<b>not markup</b>' }]] }),
    opinion('https://remote.example/notes/2', 'https://remote.example/notes/1', { summary: 'Spoilers', sensitive: true }),
    opinion('https://remote.example/notes/3', 'https://remote.example/notes/2'),
    opinion('https://remote.example/notes/4', 'https://remote.example/notes/3'),
    opinion('https://remote.example/notes/5', 'https://remote.example/notes/4'),
    { ...opinion('https://remote.example/notes/6', object), kind: 'quote' },
  ];
  const fresh = opinion('https://remote.example/notes/7', object, { local: true });
  const counts = { replies: 6, quotes: 1, likes: 2, shares: 3 };
  let calls = 0;
  await page.route((u) => u.pathname === `${PUBLIC_DOC_PATH}/opinions`, async (route) => {
    calls += 1;
    const running = calls === 1;
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ published: true, object, opinions: running ? posts : [...posts, fresh], counts, refresh: { id: '1', status: running ? 'running' : 'finished', retry: running ? 1 : 0, result_count: running ? null : 1 } }) });
  });
  await page.goto(PUBLIC_DOC_PATH, { waitUntil: 'domcontentloaded' });
  // No request before the reader opens the tab (rule D7).
  await page.waitForTimeout(300);
  expect(calls).toBe(0);
  await page.locator('#tab-op-top').click();
  const tab = page.locator('#sc-opinions-inner');
  // While the fetch runs: a progress bar with a text, not a live region (rule D8).
  await expect(tab.locator('.op-status progress')).toBeVisible();
  await expect(tab.locator('.op-status')).toContainText('다른 서버에서 의견을 가져오는 중');
  await expect(tab.locator('.op-status [aria-live], .op-status [role="status"]')).toHaveCount(0);
  await expect(tab.locator('.op-counts li')).toHaveText(['답글 6', '인용 1', '좋아요 2', '부스트 3']);
  // The page asks again after `retry` seconds and shows the new opinion with its mark; one polite message says the number.
  await expect(tab.locator('.op-status progress')).toHaveCount(0);
  expect(calls).toBe(2);
  await expect(page.locator('#sc-live')).toHaveText('의견을 가져왔습니다. 새 의견 1개.');
  await expect(tab.locator('article.is-new')).toHaveCount(1);
  await expect(tab.locator('article.is-new')).toContainText('새 의견');
  await expect(tab.locator('article.is-new')).toContainText('이 서버');
  // The tree: four levels on the page, and a link to the origin for the deeper level (rule D1).
  const first = tab.locator('.op-thread > .op-tree > .op-item').first();
  await expect(first.locator('article')).toHaveCount(4);
  await expect(first.locator('.op-continue')).toHaveText('원 서버에서 계속 (1개 더)');
  await expect(first.locator('.op-continue')).toHaveAttribute('href', 'https://remote.example/notes/4');
  // Remote text is text: the link has the rel of rule D2, the markup shows as characters, and the content warning is closed.
  const body = first.locator('article').first().locator('.op-post-body');
  await expect(body.locator('a')).toHaveAttribute('href', 'https://example.org/paper');
  await expect(body.locator('a')).toHaveAttribute('rel', 'nofollow noopener noreferrer');
  await expect(body).toContainText('<b>not markup</b>');
  await expect(body.locator('b')).toHaveCount(0);
  await expect(body.locator('br')).toHaveCount(1);
  const warning = first.locator('details.op-cw').first();
  await expect(warning.locator('summary')).toHaveText('Spoilers');
  expect(await warning.evaluate((el) => el.open)).toBe(false);
  // Quotes are a separate group (rule D1); the opinions are outside search snippets (rule D7).
  await expect(tab.locator('.op-quotes h3')).toHaveText('인용');
  await expect(tab.locator('.op-quotes article')).toHaveCount(1);
  await expect(tab.locator('.op-root[data-nosnippet]')).toHaveCount(1);
  await expect(tab.locator('.op-reply-hint')).toContainText('이 페이지의 주소');
  // A second open of the tab makes no new request.
  await page.locator('#tab-doc-top').click();
  await page.locator('#tab-op-top').click();
  await page.waitForTimeout(300);
  expect(calls).toBe(2);
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('a failed fetch of opinions shows an alert, the opinions that the server has, and a retry', async ({ page }) => {
  const errors = watchErrors(page);
  const object = 'https://claire.example.com/ap/objects/1';
  let calls = 0;
  await page.route((u) => u.pathname === `${PUBLIC_DOC_PATH}/opinions`, async (route) => {
    calls += 1;
    if (calls === 2) {
      await route.fulfill({ status: 503, contentType: 'text/plain', body: 'Unavailable' });
      return;
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ published: true, object, opinions: [opinion('https://remote.example/notes/1', object)], counts: { replies: 1, quotes: 0, likes: 0, shares: 0 }, refresh: { id: '1', status: 'failed', retry: 0, result_count: null } }) });
  });
  await page.goto(PUBLIC_DOC_PATH, { waitUntil: 'domcontentloaded' });
  await page.locator('#tab-op-top').click();
  const tab = page.locator('#sc-opinions-inner');
  await expect(tab.locator('[role="alert"]')).toContainText('다른 서버에서 의견을 가져오지 못했습니다');
  await expect(tab.locator('article')).toHaveCount(1);
  // The retry asks again; a failed request is an alert too, and the opinions stay.
  await tab.locator('.op-retry').click();
  await expect(tab.locator('[role="alert"]')).toHaveText('의견을 불러오지 못했습니다.');
  await expect(tab.locator('article')).toHaveCount(1);
  expect(calls).toBe(2);
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('the author hides an opinion from the page and shows it again (phase E7c)', async ({ page }) => {
  const errors = watchErrors(page);
  const object = 'https://claire.example.com/ap/objects/1';
  const posts = [opinion('https://remote.example/notes/1', object), { ...opinion('https://claire.example.com/ap/objects/2', object), local: true }];
  const sent = [];
  await page.route((u) => u.pathname === `${PUBLIC_DOC_PATH}/opinions`, async (route) => {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ published: true, object, opinions: posts, counts: { replies: 2, quotes: 0, likes: 0, shares: 0 }, refresh: null, moderation: { url: '/api/federation/opinions/hide' } }) });
  });
  await page.route((u) => u.pathname === '/api/federation/opinions/hide', async (route) => {
    const body = route.request().postDataJSON();
    sent.push(body);
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ hidden: body.hidden }) });
  });
  await page.goto(PUBLIC_DOC_PATH, { waitUntil: 'domcontentloaded' });
  await page.locator('#tab-op-top').click();
  const tab = page.locator('#sc-opinions-inner');
  // A local reply is the note of an account of this server: it has no hide control.
  await expect(tab.locator('.op-hide')).toHaveCount(1);
  const remote = tab.locator('article').first();
  await remote.locator('.op-hide').click();
  await expect(remote.locator('.op-hide')).toHaveText('다시 보이기');
  await expect(remote).toHaveClass(/is-hidden/);
  await expect(remote.locator('.op-post-meta')).toContainText('숨김');
  await expect(page.locator('#sc-live')).toHaveText('의견을 이 페이지에서 숨겼습니다.');
  await remote.locator('.op-hide').click();
  await expect(remote.locator('.op-hide')).toHaveText('이 페이지에서 숨기기');
  await expect(page.locator('#sc-live')).toHaveText('의견을 다시 보입니다.');
  expect(sent).toEqual([{ object, opinion: 'https://remote.example/notes/1', hidden: true }, { object, opinion: 'https://remote.example/notes/1', hidden: false }]);
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('private link_access=1 renders opaque IDs e0..eN, blocks other documents, hides workspace link, and shows non-federated notice in Opinions', async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 1280, height: 800 });

  await page.route((u) => u.pathname === LINK_ONLY_PATH, async (route) => {
    const req = route.request();
    if (req.resourceType() !== 'document') return route.fallback();
    const res = await route.fetch({ url: new URL(PUBLIC_DOC_PATH, req.url()).toString() });
    let html = patchDocDataInHtml(await res.text(), (doc) => {
      delete doc.nodes;
      doc.silvercord = {
        token: LINK_ONLY_TOKEN,
        kb_label: '',
        kb_public: false,
        can_open_workspace: false,
        workspace_url: null,
        graph_url: LINK_ONLY_GRAPH_PATH,
        object_id: null,
        entity_count: 2,
        link_only: true,
      };
    });
    html = html.replace('<meta name="robots" content="index, follow, max-snippet:-1, max-image-preview:large, max-video-preview:-1"/>', '<meta name="robots" content="noindex, nofollow"/>');
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html, headers: { 'X-Robots-Tag': 'noindex, nofollow' } });
  });

  await page.route((u) => u.pathname === LINK_ONLY_GRAPH_PATH, async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        scope: 'link_only',
        document: { token: LINK_ONLY_TOKEN, title: '테스트 문서 1 (핵심 개념)' },
        ring1: [
          { id: 'e0', label: '비공개 개념 A', group: 'Concept', degree: 2, aliases: [], observations: ['비공개 문서 내 관찰'], entity_path: null, other_docs: [], hidden_other_docs_count: 1 },
          { id: 'e1', label: '비공개 도구 B', group: 'Tool', degree: 1, aliases: [], observations: [], entity_path: null, other_docs: [], hidden_other_docs_count: 0 },
        ],
        ring2: [
          { id: 'e2', label: '이웃 모델 C', group: 'Model', degree: 3, aliases: [], observations: [], entity_path: null, other_docs: [], hidden_other_docs_count: 2, connected_r1: ['e0'] },
        ],
        edges: [
          { from: 'e0', to: 'e1', rel: 'relates_to', provisional: false },
          { from: 'e0', to: 'e2', rel: 'uses', provisional: true },
        ],
        unshown_r1: 0,
        unshown_r2: 0,
        shared_docs: [],
        hidden_shared_docs_count: 2,
      }),
    });
  });

  await page.goto(LINK_ONLY_PATH, { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#sc-kb-badge')).toHaveText('링크 공개 문서');
  await expect(page.locator('#sc-ws-link')).toBeHidden();
  await expect(page.locator('meta[name="robots"]')).toHaveAttribute('content', 'noindex, nofollow');

  // Opinions tab shows non-federated private KB notice instead of public sample thread.
  await page.locator('#tab-op-top').click();
  await expect(page.locator('#op-private-content')).toBeVisible();
  await expect(page.locator('#op-private-content')).toContainText('비공개 지식베이스 문서');
  await expect(page.locator('#sc-opinions-inner .op-root')).toHaveCount(0);

  // Graph panel shows Ring 1 + Ring 2 with opaque IDs and blocks other documents.
  await page.locator('#tab-doc-top').click();
  await page.locator('#sc-graph-toggle-top').click();
  const aside = page.locator('#sc-desktop-aside');
  await expect(aside.locator('svg.ctx-local-graph .ctx-lg-node.ring-1')).toHaveCount(2);
  await expect(aside.locator('svg.ctx-local-graph .ctx-lg-node.ring-2')).toHaveCount(1);

  const entityIds = await aside.locator('[data-entity-id]').evaluateAll((els) => els.map((el) => el.getAttribute('data-entity-id')));
  expect(entityIds.length).toBeGreaterThan(0);
  for (const eid of entityIds) {
    expect(eid).toMatch(/^e\d+$/);
  }
  const asideHtml = await aside.innerHTML();
  expect(asideHtml).not.toContain('ent-');
  expect(asideHtml).not.toContain('ent_');

  await expect(aside.locator('.gp-blocked-box')).toBeVisible();
  await expect(aside.locator('.gp-blocked-box')).toContainText('타 문서 열람 차단됨');
  await expect(aside.locator('.gp-blocked-box')).toContainText('2건');
  await expect(aside.locator('a.sc-gp-row')).toHaveCount(0);

  // Entity Detail uses opaque ID and keeps other documents and private entity page blocked.
  await aside.locator('.ctx-lg-node[data-entity-id="e0"]').click();
  await expect(aside.locator('.gp-detail-id code')).toHaveText('e0');
  await expect(aside.locator('.gp-detail-note').first()).toContainText('1건');
  await expect(aside.locator('a[href^="/entity/"]')).toHaveCount(0);
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});
