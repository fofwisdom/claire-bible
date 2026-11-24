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

// Emoji detection pattern matching tests/test_ui_line_icons.py (zero emojis in Silvercord chrome and Opinions prototype).
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

test('mobile bottom bar switches doc and opinions preserving scrollY, syncs srail, opens modal dialog sheet, and renders emoji-free Opinions prototype', async ({ page }) => {
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

  // Switch to 의견 tab: #srail hides and ActivityPub prototype shows disabled controls and zero emojis.
  await page.locator('#tab-op-bot').click();
  await expect(page.locator('#tab-op-bot')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#tab-doc-bot')).toHaveAttribute('aria-selected', 'false');
  await expect(page.locator('#sc-panel-opinions')).toBeVisible();
  await expect(page.locator('#sc-panel-doc')).toHaveAttribute('hidden', 'until-found');
  await expect(page.locator('#srail')).toBeHidden();

  await expect(page.locator('#op-public-content')).toBeVisible();
  await expect(page.locator('#op-public-content textarea[disabled][aria-disabled="true"]')).toHaveCount(1);
  await expect(page.locator('#op-public-content .op-disabled-btn[disabled][aria-disabled="true"]')).toHaveCount(3);
  await expect(page.locator('#op-public-content .op-thread-box .op-post')).toHaveCount(2);

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
  await expect(page.locator('#op-public-content')).toHaveCount(0);

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
