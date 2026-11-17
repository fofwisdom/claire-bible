// Federation client view of /syndication (FEDERATION_REMOTE_READING_DESIGN.md Sections 1.3 and 8, unit FP4-5).
// The server of this test directory is in single mode, and the federation routes exist in network mode only. This file gives the page the network mode and answers the federation routes itself. The routes have their own end-to-end tests with a real app in network mode: tests/test_e2e_federation_client.py.
const { test, expect } = require('@playwright/test');

const NOW = Math.floor(Date.now() / 1000);
const author = { iri: 'https://remote.example/users/carol', name: 'Carol <script>window.__pwned = 1</script>', handle: '@carol@remote.example' };

function item(id, changes) {
  return Object.assign({
    id: String(id), object_iri: 'https://remote.example/notes/' + id, url: 'https://remote.example/@carol/' + id, type: 'Note', author, announced_by: null, name: null, summary: null, sensitive: false,
    text: 'Plain text of post ' + id + ' <img src=x onerror="window.__pwned = 1">', language: 'en', attachments: 0, in_reply_to: null, in_timeline: true, published_at: NOW - 600, updated_at: null, received_at: NOW - 500, unread: true, starred: false, mention: false, linked: null,
  }, changes || {});
}

const ITEMS = [item(9, { announced_by: { iri: 'https://forum.example/c/rust', name: 'Rust', handle: '@rust@forum.example' }, name: 'A forum post' }), item(8, { summary: 'Spoiler', sensitive: true, text: '' }), item(7)];
const COUNTS = { total: 3, unread: 3, starred: 0, mentions: 0, unread_mentions: 0, ingested: 0, published: 2 };
// The body that the frame gets. The real route sanitizes the HTML and sends a policy; here the sandbox attribute of the frame alone must stop the script.
const FRAME_BODY = '<!doctype html><html><body><p id="body">Body of the post</p><script>document.getElementById("body").textContent = "SCRIPT RAN"; parent.postMessage("pwned", "*");</script></body></html>';

test.describe('federation client view (FP4-5)', () => {
  // A request that the service worker sends does not go through page.route().
  test.use({ serviceWorkers: 'block' });

  test('FP4-5: the stage bar switches between the client and the knowledge base, the body renders in a sandbox frame, and the context menu has the Markdown export', async ({ page }) => {
    const pageErrors = [];
    const patches = [];
    const threadFetch = { calls: 0, done: false, requests: [] };
    // With service workers blocked, the test runner puts its own script into each frame. In the sandbox frame that script fails; it is not a script of the page or of the post.
    page.on('pageerror', error => { if (!error.message.includes('Service worker is disabled because the context is sandboxed')) pageErrors.push(error.message); });
    await page.setViewportSize({ width: 1400, height: 900 });
    // The page config says "single" on this server. The client view reads the mode from it.
    await page.addInitScript(() => {
      let config;
      Object.defineProperty(window, '__CLAIRE_CONFIG', { configurable: true, get: () => config, set: value => { config = Object.assign({}, value, { serverMode: 'network' }); } });
      window.addEventListener('message', event => { if (event.data === 'pwned') window.__pwned = 1; });
    });
    const json = body => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    await page.route('**/api/federation/timeline?**', route => route.fulfill(json({ items: ITEMS, next_max_id: null, counts: COUNTS })));
    await page.route('**/api/federation/timeline/*', route => {
      const id = route.request().url().split('/').pop();
      const found = ITEMS.find(entry => entry.id === id);
      if (route.request().method() === 'PATCH') {
        const change = route.request().postDataJSON();
        patches.push({ id, change });
        Object.assign(found, change);
        return route.fulfill(json({ item: found, counts: Object.assign({}, COUNTS, { unread: ITEMS.filter(entry => entry.unread).length, starred: ITEMS.filter(entry => entry.starred).length }) }));
      }
      const detail = Object.assign({}, found, { render_url: '/render/remote/' + id + '?t=test' });
      // Thread fetch (FP4-6): after the fetch task of post 8 has a result, its thread has one more post.
      const fetchedReply = item(10, { in_reply_to: found.object_iri, in_timeline: false, render_url: '/render/remote/10?t=test' });
      return route.fulfill(json({ item: detail, thread: id === '8' && threadFetch.done ? [detail, fetchedReply] : [detail] }));
    });
    // Thread fetch (FP4-6): the first request for post 8 makes a task that waits; the next one says that the task has a result. Other posts have a recent fetch.
    await page.route('**/api/federation/timeline/*/context', route => {
      threadFetch.requests.push(route.request().method() + ' ' + new URL(route.request().url()).pathname);
      if (!route.request().url().includes('/timeline/8/')) return route.fulfill(json({ state: 'recent', pending: false }));
      threadFetch.calls += 1;
      if (threadFetch.calls === 1) return route.fulfill({ status: 202, contentType: 'application/json', body: JSON.stringify({ state: 'queued', pending: true }) });
      threadFetch.done = true;
      return route.fulfill(json({ state: 'recent', pending: false }));
    });
    await page.route('**/render/remote/**', route => route.fulfill({ status: 200, contentType: 'text/html', body: FRAME_BODY }));
    await page.route('**/api/federation/following?**', route => route.fulfill(json({ total: 1, limit: 1000, items: [{ id: '1', request: '@carol@remote.example', actor_iri: author.iri, actor_type: 'Person', display_name: 'Carol', state: 'accepted', created_at: 1, updated_at: 1 }], next_max_id: null })));
    await page.route('**/api/federation/status', route => route.fulfill(json({ federation: 'on', handle: '@owner@claire.example', actor_id: 'https://claire.example/ap/actors/1', is_actor: true, actor_state: 'active', followers: { total: 0 }, pending_tasks: 0 })));
    await page.route('**/api/federation/objects?**', route => route.fulfill(json({ total: 0, items: [], next_max_id: null })));

    // A login token of the seed database is for one login. This test has its own token.
    await page.goto('/?t=e2e-fedclient-token-0123456789abcd', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    await page.goto('/syndication?stage=collect', { waitUntil: 'domcontentloaded' });

    // Stage 1: the client view. The knowledge base view is the same page, not in view.
    await expect(page.locator('#stagebar')).toBeVisible();
    await expect(page.locator('[data-stage-btn="collect"]')).toHaveAttribute('aria-current', 'page');
    await expect(page.locator('#panel-client')).toBeVisible();
    await expect(page.locator('#panel-kb')).toBeHidden();
    await expect(page.locator('#stage-collect-counts')).toHaveText('안 읽음 3 · 멘션 0');
    const posts = page.locator('.synd-post');
    await expect(posts).toHaveCount(3);
    // The list has text only: a tag in a name or in the text shows as characters.
    await expect(posts.nth(2).locator('.synd-post-author')).toHaveText('Carol <script>window.__pwned = 1</script>');
    await expect(posts.nth(2).locator('.synd-post-text')).toContainText('<img src=x onerror=');
    await expect(page.locator('#client-list img, #client-list script')).toHaveCount(0);
    await expect(posts.nth(0)).toContainText('Rust 님이 부스트');
    await expect(page.locator('.synd-follow-row')).toContainText('Carol');

    // The body is a frame without the permission for scripts. The script of the body does not run.
    await posts.nth(2).click();
    const frameEl = page.locator('iframe.synd-frame');
    await expect(frameEl).toHaveAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
    await expect(page.frameLocator('iframe.synd-frame').locator('#body')).toHaveText('Body of the post');
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    // Opening a post marks it as read.
    await expect.poll(() => patches.some(p => p.id === '7' && p.change.unread === false)).toBe(true);
    await expect(page.locator('#stage-collect-counts')).toHaveText('안 읽음 2 · 멘션 0');

    // A post with a content warning shows the summary first. The frame loads after a click.
    await posts.nth(1).click();
    await expect(page.locator('#client-reader [data-open-warning]')).toBeVisible();
    await expect(page.locator('#client-reader iframe')).toHaveCount(0);
    await page.locator('#client-reader [data-open-warning]').click();
    await expect(page.locator('#client-reader iframe')).toHaveCount(1);

    // Thread fetch (FP4-6): opening a post asks for its thread. While the task waits, the reader shows a note; then the thread loads again with the fetched post.
    expect(threadFetch.requests[0]).toBe('POST /api/federation/timeline/7/context');
    await expect(page.locator('#client-thread-fetch')).toBeVisible();
    await expect(page.locator('#client-thread-fetch')).toHaveText('이 글타래의 게시물을 더 가져오는 중…');
    await expect(page.locator('.synd-thread-title')).toHaveText('글타래 · 게시물 2개');
    await expect(page.locator('#client-thread-fetch')).toBeHidden();
    await expect(page.locator('#client-reader .synd-entry')).toHaveCount(2);
    expect(threadFetch.calls).toBe(2);

    // The star is a change of the item.
    await posts.nth(0).locator('[data-item-star]').click();
    await expect.poll(() => patches.some(p => p.id === '9' && p.change.starred === true)).toBe(true);
    await expect(posts.nth(0).locator('[data-item-star]')).toHaveAttribute('aria-pressed', 'true');

    // The ingest dialog of one item has the themes of the owner.
    await page.locator('#client-reader [data-item-ingest]').click();
    await expect(page.locator('#modal-item-ingest')).toHaveAttribute('open', '');
    await expect(page.locator('#item-ingest-theme')).toHaveCount(0);  // an ingest writes only to the personal knowledge base: no selector
    await page.keyboard.press('Escape');

    // Stage 2: the knowledge base view of the same page.
    await page.locator('[data-stage-btn="ingest"]').click();
    await expect(page).toHaveURL(/\/syndication\?stage=ingest$/);
    await expect(page.locator('#panel-kb')).toBeVisible();
    await expect(page.locator('#panel-client')).toBeHidden();
    const row = page.locator('#headlines-container [data-row-id]').first();
    await expect(row).toBeVisible();

    // Context menu (right click) of a document: the Markdown export gives a file.
    await row.click({ button: 'right' });
    const menu = page.locator('#pop-doc-context');
    await expect(menu).toBeVisible();
    await expect(menu.locator('[data-art-cmd="export-md"]')).toHaveText('Markdown 내보내기');
    const [download] = await Promise.all([page.waitForEvent('download'), menu.locator('[data-art-cmd="export-md"]').click()]);
    expect(download.suggestedFilename()).toMatch(/\.md$/);
    await expect(menu).toBeHidden();

    // Stage 3: the filter of published and unpublished documents.
    await page.locator('[data-stage-btn="publish"]').click();
    await expect(page.locator('#pub-filter')).toBeVisible();
    await expect(page.locator('[data-pub-filter="unpublished"]')).toHaveAttribute('aria-pressed', 'true');
    await page.locator('[data-pub-filter="published"]').click();
    await expect(page.locator('#headlines-container [data-row-id]')).toHaveCount(0);

    // The command palette has the three stages.
    await page.keyboard.press('/');
    await expect(page.locator('#cmdpalette')).toHaveAttribute('open', '');
    await page.locator('#cmdpalette-input').fill('수집');
    await page.keyboard.press('Enter');
    await expect(page.locator('#panel-client')).toBeVisible();
    expect(pageErrors).toEqual([]);
  });

  test('FP4-5: in single mode stage 1 shows a notice and no timeline request', async ({ page }) => {
    await page.goto('/?t=e2e-fedsingle-token-0123456789abcd', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    // The requests of /syndication only. The home page asks the timeline counts for its stage strip, also in single mode.
    const requests = [];
    page.on('request', request => { if (request.url().includes('/api/federation/')) requests.push(request.url()); });
    await page.goto('/syndication', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('#stagebar')).toBeVisible();
    await expect(page.locator('#panel-kb')).toBeVisible();
    await page.locator('[data-stage-btn="collect"]').click();
    await expect(page.locator('#client-notice')).toContainText('네트워크 모드');
    await expect(page.locator('.synd-post')).toHaveCount(0);
    expect(requests.filter(url => url.includes('/timeline'))).toEqual([]);
  });
});
