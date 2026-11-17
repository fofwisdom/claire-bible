// Feed view of the federation client of /syndication (FEDERATION_REMOTE_READING_DESIGN.md Section 1.3 rule 7, unit FP4-F3).
// The server of this test directory is in single mode: the feed view works there. This file answers the feed routes itself, so that the test needs no feed server. The routes have their own end-to-end test with a real app and a local feed server: tests/test_e2e_feed_view.py.
const { test, expect } = require('@playwright/test');

const NOW = Math.floor(Date.now() / 1000);
// The body that the frame gets. The real route sanitizes the HTML and sends a policy; here the sandbox attribute of the frame alone must stop the script.
const FRAME_BODY = '<!doctype html><html><body><p id="body">Body of the item</p><script>document.getElementById("body").textContent = "SCRIPT RAN"; parent.postMessage("pwned", "*");</script></body></html>';

function feedItem(id, changes) {
  return Object.assign({
    id, feed_id: 3, feed_title: 'Garden <b>News</b>', feed_folder: 'Garden', url: 'https://garden.example/posts/' + id, title: 'Item ' + id + ' <script>window.__pwned = 1</script>', author: 'Kim', summary: null, categories: ['vegetables'],
    published_at: NOW - 600, modified_at: null, fetched_at: NOW - 500, unread: true, starred: false, linked_kb_id: null, linked_doc_id: null, text: 'Plain text of item ' + id + ' <img src=x onerror="window.__pwned = 1">', linked: null, timeline_item_id: null,
  }, changes || {});
}

test.describe('feed view (FP4-F3)', () => {
  // A request that the service worker sends does not go through page.route().
  test.use({ serviceWorkers: 'block' });

  test('FP4-F3: subscribe to a feed, read an item in a sandbox frame, star it, and take it into a theme', async ({ page }) => {
    const pageErrors = [];
    const calls = [];
    page.on('pageerror', error => { if (!error.message.includes('Service worker is disabled because the context is sandboxed')) pageErrors.push(error.message); });
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.addInitScript(() => { window.addEventListener('message', event => { if (event.data === 'pwned') window.__pwned = 1; }); });

    const feeds = [];
    const items = [feedItem(6), feedItem(5)];
    const json = (body, status) => ({ status: status || 200, contentType: 'application/json', body: JSON.stringify(body) });
    const feedView = () => feeds.map(feed => Object.assign({}, feed, { items: items.length, unread: items.filter(entry => entry.unread).length }));
    await page.route('**/render/feed/**', route => route.fulfill({ status: 200, contentType: 'text/html', body: FRAME_BODY }));
    await page.route('**/api/feeds**', route => {
      const request = route.request();
      const url = new URL(request.url());
      const method = request.method();
      const body = method === 'GET' ? null : request.postDataJSON();
      calls.push({ method, path: url.pathname, body });
      if (url.pathname === '/api/feeds/discover') {
        return route.fulfill(json({ url: body.url, error: null, feeds: [{ url: 'https://garden.example/feed.xml', title: 'Garden feed', format: 'rss', subscribed: false }, { url: 'https://garden.example/comments.xml', title: 'Comments', format: 'atom', subscribed: false }] }));
      }
      if (url.pathname === '/api/feeds' && method === 'GET') return route.fulfill(json({ feeds: feedView(), folders: ['Garden'], limit: 1000 }));
      if (url.pathname === '/api/feeds' && method === 'POST') {
        feeds.push({ id: 3, url: body.url, site_url: 'https://garden.example/', title: 'Garden <b>News</b>', folder: body.folder || '', format: 'rss', websub_hub: null, last_polled_at: null, last_success_at: null, next_poll_at: null, last_status: null, last_error: null, failure_count: 0, created_at: NOW });
        return route.fulfill(json({ feed: feeds[0], created: true }, 201));
      }
      if (url.pathname === '/api/feeds/poll') return route.fulfill(json({ polled: 1, not_polled: 0, outcomes: [{ feed_id: 3, result: 'updated', http_status: 200, added: 2, updated: 0, error: null, next_poll_at: NOW + 3600 }] }));
      if (url.pathname === '/api/feeds/items' && method === 'GET') return route.fulfill(json({ items: feeds.length ? items : [], next_before_id: null }));
      if (url.pathname === '/api/feeds/items' && method === 'PATCH') {
        const changed = items.filter(entry => (body.item_ids || []).includes(entry.id));
        changed.forEach(entry => { if ('unread' in body) entry.unread = body.unread; if ('starred' in body) entry.starred = body.starred; });
        return route.fulfill(json({ changed: changed.length }));
      }
      const ingest = url.pathname.match(/^\/api\/feeds\/items\/(\d+)\/ingest$/);
      if (ingest) {
        const found = items.find(entry => String(entry.id) === ingest[1]);
        found.linked = { kb_id: 0, document_id: 'doc_from_feed' };
        return route.fulfill(json({ document_id: 'doc_from_feed', linked: found.linked }));
      }
      const one = url.pathname.match(/^\/api\/feeds\/items\/(\d+)$/);
      if (one) return route.fulfill(json({ item: Object.assign({}, items.find(entry => String(entry.id) === one[1]), { has_body: true, render_url: '/render/feed/' + one[1] + '?t=test' }) }));
      return route.fulfill(json({ error: 'not_found' }, 404));
    });

    // A login token of the seed database is for one login. This test has its own token.
    await page.goto('/?t=e2e-feedview-token-0123456789abcde', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    await page.goto('/syndication?stage=collect', { waitUntil: 'domcontentloaded' });

    // Single mode: the feeds, and no stream tree and no follow tree of the timeline.
    await expect(page.locator('#panel-client')).toBeVisible();
    await expect(page.locator('#client-feeds-folder')).toBeVisible();
    await expect(page.locator('#client-streams-folder')).toBeHidden();
    await expect(page.locator('#client-following-folder')).toBeHidden();
    await expect(page.locator('#stage-collect-counts')).toHaveText('피드: 읽지 않음 0개');
    await expect(page.locator('#client-feeds')).toContainText('아직 피드가 없습니다');

    // Subscribe: the address of a site gives two feeds. The owner selects one. The first poll gives the items.
    await page.locator('#client-feed-url').fill('garden.example');
    await page.locator('#client-feed-folder').fill('Garden');
    await page.locator('#client-feed-form button[type="submit"]').click();
    await expect(page.locator('#client-feed-candidates [data-feed-candidate]')).toHaveCount(2);
    expect(calls.find(call => call.path === '/api/feeds/discover').body).toEqual({ url: 'https://garden.example' });
    await page.locator('#client-feed-candidates [data-feed-candidate]').first().click();
    await expect.poll(() => calls.some(call => call.path === '/api/feeds' && call.method === 'POST' && call.body.url === 'https://garden.example/feed.xml' && call.body.folder === 'Garden')).toBe(true);
    await expect.poll(() => calls.some(call => call.path === '/api/feeds/poll' && call.body.feed_id === 3)).toBe(true);

    // The tree has the folder and the feed with its unread count. A tag in a title shows as characters.
    const feedButton = page.locator('#client-feeds [data-feed-scope="feed:3"]');
    await expect(feedButton).toContainText('Garden <b>News</b>');
    await expect(feedButton.locator('.tt-count-pill')).toHaveText('2');
    await expect(page.locator('#client-feeds [data-feed-scope="folder:Garden"]')).toBeVisible();
    await expect(feedButton).toHaveClass(/active/);
    await expect(page.locator('#stage-collect-counts')).toHaveText('피드: 읽지 않음 2개');

    // The list has text only.
    const posts = page.locator('#client-list .synd-post');
    await expect(posts).toHaveCount(2);
    await expect(posts.nth(1).locator('.synd-post-title')).toHaveText('Item 5 <script>window.__pwned = 1</script>');
    await expect(posts.nth(1).locator('.synd-post-text')).toContainText('<img src=x onerror=');
    await expect(page.locator('#client-list img, #client-list script, #client-feeds b')).toHaveCount(0);

    // Read an item: the body is a frame without the permission for scripts. The script of the body does not run.
    await posts.nth(1).click();
    const frameEl = page.locator('#client-reader iframe.synd-frame');
    await expect(frameEl).toHaveAttribute('sandbox', 'allow-popups allow-popups-to-escape-sandbox');
    await expect(frameEl).toHaveAttribute('src', '/render/feed/5?t=test');
    await expect(page.frameLocator('#client-reader iframe.synd-frame').locator('#body')).toHaveText('Body of the item');
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    await expect(page.locator('#client-reader script, #client-reader img')).toHaveCount(0);
    // Opening an item marks it as read.
    await expect.poll(() => calls.some(call => call.method === 'PATCH' && call.body.item_ids[0] === 5 && call.body.unread === false)).toBe(true);
    await expect(feedButton.locator('.tt-count-pill')).toHaveText('1');
    await expect(posts.nth(1)).not.toHaveClass(/is-unread/);

    // The star is a change of the item. The key "j" or "k" moves in the list.
    await page.locator('#client-reader [data-feed-star]').click();
    await expect.poll(() => calls.some(call => call.method === 'PATCH' && call.body.item_ids[0] === 5 && call.body.starred === true)).toBe(true);
    await expect(posts.nth(1).locator('[data-feed-star]')).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#client-reader [data-feed-share]')).toBeVisible();

    // Take the item into a theme: the dialog of the timeline items, with the feed route.
    await page.locator('#client-reader [data-feed-ingest]').click();
    await expect(page.locator('#modal-item-ingest')).toHaveAttribute('open', '');
    await expect(page.locator('#modal-item-ingest-title')).toHaveText('이 피드 항목을 지식베이스에 적재');
    await expect(page.locator('#item-ingest-theme')).toHaveCount(0);  // an ingest writes only to the personal knowledge base: no selector
    await page.locator('#btn-item-ingest').click();
    await expect.poll(() => calls.some(call => call.path === '/api/feeds/items/5/ingest' && call.method === 'POST' && call.body.theme === undefined)).toBe(true);  // the request names no knowledge base
    await expect(page.locator('#modal-item-ingest')).not.toHaveAttribute('open', '');
    await expect(page.locator('#client-reader [data-feed-doc]')).toBeVisible();
    await expect(page.locator('#client-reader [data-feed-ingest]')).toHaveCount(0);
    // No request went to the route of the timeline items.
    expect(calls.some(call => call.path.includes('/api/federation/'))).toBe(false);

    // "Unread only" is a filter of the list request.
    await page.locator('[data-feed-unread-only]').click();
    await expect(page.locator('[data-feed-unread-only]')).toHaveAttribute('aria-pressed', 'true');
    expect(pageErrors).toEqual([]);
  });
});
