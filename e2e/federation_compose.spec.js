// Compose controls of the federation client in /syndication (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.3 and 5.4, unit FP4-9).
// The server of this test directory is in single mode, and the federation routes exist in network mode only. This file gives the page the network mode and answers the federation routes itself, as federation_client.spec.js does. The routes have their own end-to-end test with a real app in network mode: tests/test_e2e_federation_outbound.py.
const { test, expect } = require('@playwright/test');

const NOW = Math.floor(Date.now() / 1000);
const carol = { iri: 'https://remote.example/users/carol', name: 'Carol', handle: '@carol@remote.example' };
const me = { iri: 'http://127.0.0.1:8766/ap/actors/1', name: 'Owner', handle: '@owner@127.0.0.1:8766' };

function item(id, changes) {
  return Object.assign({
    id: String(id), object_iri: 'https://remote.example/notes/' + id, url: 'https://remote.example/@carol/' + id, type: 'Note', author: carol, announced_by: null, name: null, summary: null, sensitive: false,
    text: 'Plain text of post ' + id, language: 'en', attachments: 0, in_reply_to: null, in_timeline: true, published_at: NOW - 600, updated_at: null, received_at: NOW - 500, unread: false, starred: false, mention: false, linked: null,
    favourited: false, boosted: false, note: null,
  }, changes || {});
}

test.describe('compose controls of the federation client (FP4-9)', () => {
  // A request that the service worker sends does not go through page.route().
  test.use({ serviceWorkers: 'block' });

  test('FP4-9: reply, new note, edit, delete, favourite, and boost send the requests of the routes, and note text shows as characters', async ({ page }) => {
    const pageErrors = [];
    const sent = [];
    page.on('pageerror', error => { if (!error.message.includes('Service worker is disabled because the context is sandboxed')) pageErrors.push(error.message); });
    page.on('dialog', dialog => dialog.accept());
    await page.setViewportSize({ width: 1400, height: 900 });
    await page.addInitScript(() => {
      let config;
      Object.defineProperty(window, '__CLAIRE_CONFIG', { configurable: true, get: () => config, set: value => { config = Object.assign({}, value, { instanceMode: 'network' }); } });
    });
    // An own note with a script in its text, and a post of a remote actor.
    const own = item(8, { object_iri: 'http://127.0.0.1:8766/ap/objects/900', author: me, text: 'My note <img src=x onerror="window.__pwned = 1">', note: { id: '900', content: 'My note <img src=x onerror="window.__pwned = 1">', summary: null, sensitive: false, language: 'en', edited_at: NOW - 60, likes: 2, announces: 1 } });
    const items = [own, item(7)];
    const json = (body, status) => ({ status: status || 200, contentType: 'application/json', body: JSON.stringify(body) });
    const counts = { total: 2, unread: 0, starred: 0, mentions: 0, unread_mentions: 0, ingested: 0, published: 0, private_messages: 0 };
    await page.route('**/api/federation/timeline?**', route => route.fulfill(json({ items, next_max_id: null, counts })));
    await page.route('**/api/federation/timeline/*', route => {
      const found = items.find(entry => entry.id === route.request().url().split('/').pop());
      const detail = Object.assign({}, found, { render_url: '/render/remote/' + found.id + '?t=test' });
      return route.fulfill(json({ item: detail, thread: [detail] }));
    });
    await page.route('**/api/federation/timeline/*/*', route => {
      const [id, name] = route.request().url().split('/').slice(-2);
      const found = items.find(entry => entry.id === id);
      sent.push({ method: route.request().method(), path: `/timeline/${id}/${name}` });
      found[name === 'favourite' ? 'favourited' : 'boosted'] = route.request().method() === 'POST';
      return route.fulfill(json({ item_id: id, favourited: found.favourited, boosted: found.boosted }));
    });
    await page.route('**/api/federation/notes', route => {
      const body = route.request().postDataJSON();
      sent.push({ method: 'POST', path: '/notes', body });
      if (body.content.includes('too long')) return route.fulfill(json({ error: 'content_too_long', limit: 500 }, 400));
      return route.fulfill(json({ note: { id: '901' }, item_id: '9' }, 201));
    });
    await page.route('**/api/federation/notes/*', route => {
      sent.push({ method: route.request().method(), path: '/notes/' + route.request().url().split('/').pop(), body: route.request().postDataJSON() });
      if (route.request().method() === 'DELETE') { items.splice(items.indexOf(own), 1); return route.fulfill(json({ deleted: true, id: '900' })); }
      return route.fulfill(json({ note: Object.assign({}, own.note, route.request().postDataJSON()) }));
    });
    await page.route('**/render/remote/**', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><html><body><p>Body</p></body></html>' }));
    await page.route('**/api/federation/following?**', route => route.fulfill(json({ total: 0, limit: 1000, items: [], next_max_id: null })));
    await page.route('**/api/federation/status', route => route.fulfill(json({ federation: 'on', handle: me.handle, actor_id: me.iri, is_actor: true, actor_state: 'active', followers: { total: 0 }, pending_tasks: 0 })));
    await page.route('**/api/federation/objects?**', route => route.fulfill(json({ total: 0, items: [], next_max_id: null })));

    // A login token of the seed database is for one login. This test has its own token.
    await page.goto('/?t=e2e-fedcompose-token-0123456789abc', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    await page.goto('/syndication?stage=collect', { waitUntil: 'domcontentloaded' });
    const posts = page.locator('.synd-post');
    await expect(posts).toHaveCount(2);

    // A post of a remote actor has reply, favourite, and boost. An own note has reply, edit, and delete, with its counters.
    const remote = posts.nth(1);
    await expect(remote.locator('[data-note-reply], [data-item-favourite], [data-item-boost]')).toHaveCount(3);
    await expect(remote.locator('[data-note-edit], [data-note-delete]')).toHaveCount(0);
    const mine = posts.nth(0);
    await expect(mine.locator('[data-note-reply], [data-note-edit], [data-note-delete]')).toHaveCount(3);
    await expect(mine.locator('[data-item-favourite], [data-item-boost]')).toHaveCount(0);
    await expect(mine.locator('.synd-note-counters')).toHaveText('좋아요 2 · 부스트 1 · 수정됨');
    // The text of a note shows as characters: no element comes from it.
    await expect(mine.locator('.synd-post-text')).toContainText('<img src=x onerror=');
    await expect(page.locator('#client-list img')).toHaveCount(0);
    // Each control is a line icon with a name for assistive technology.
    await expect(remote.locator('[data-item-favourite] svg')).toHaveCount(1);
    await expect(remote.locator('[data-item-favourite]')).toHaveAttribute('aria-label', '좋아요');

    // Favourite and boost: the request, then the pressed state. A second click is the undo.
    await remote.locator('[data-item-favourite]').click();
    await expect(remote.locator('[data-item-favourite]')).toHaveAttribute('aria-pressed', 'true');
    await remote.locator('[data-item-boost]').click();
    await expect(remote.locator('[data-item-boost]')).toHaveAttribute('aria-pressed', 'true');
    await remote.locator('[data-item-favourite]').click();
    await expect(remote.locator('[data-item-favourite]')).toHaveAttribute('aria-pressed', 'false');
    expect(sent.map(s => `${s.method} ${s.path}`)).toEqual(['POST /timeline/7/favourite', 'POST /timeline/7/boost', 'DELETE /timeline/7/favourite']);

    // Reply: the dialog starts with the handle of the author. An error of the route shows in the dialog.
    await remote.locator('[data-note-reply]').click();
    const dialog = page.locator('#modal-note-compose');
    await expect(dialog).toHaveAttribute('open', '');
    await expect(page.locator('#modal-note-compose-title')).toHaveText('답글');
    await expect(page.locator('#note-compose-context')).toHaveText('Carol에게 답글');
    await expect(page.locator('#note-compose-text')).toHaveValue('@carol@remote.example ');
    await page.locator('#note-compose-text').fill('@carol@remote.example too long');
    await page.locator('#btn-note-compose').click();
    await expect(page.locator('#note-compose-msg')).toHaveText('본문이 너무 깁니다. 한도는 500자입니다.');
    await page.locator('#note-compose-text').fill('@carol@remote.example Thank you #Claire');
    await page.locator('#note-compose-summary').fill('A warning');
    await page.locator('#note-compose-language').fill('ko');
    await expect(page.locator('#note-compose-count')).toHaveText('39자');
    await page.screenshot({ path: 'test-results/federation-compose.png' });
    await page.locator('#btn-note-compose').click();
    await expect(dialog).not.toHaveAttribute('open', '');
    expect(sent.at(-1)).toEqual({ method: 'POST', path: '/notes', body: { content: '@carol@remote.example Thank you #Claire', summary: 'A warning', language: 'ko', in_reply_to: '7' } });

    // New note: no reply target and no handle.
    await page.locator('#btn-note-new').click();
    await expect(page.locator('#modal-note-compose-title')).toHaveText('새 노트');
    await expect(page.locator('#note-compose-text')).toHaveValue('');
    await expect(page.locator('#note-compose-context')).toBeHidden();
    await page.locator('#note-compose-text').fill('Hello');
    await page.locator('#btn-note-compose').click();
    expect(sent.at(-1)).toEqual({ method: 'POST', path: '/notes', body: { content: 'Hello' } });

    // Edit: the dialog has the plain text of the note. The reader has the same controls with their text.
    await posts.nth(0).click();
    const entry = page.locator('#client-reader .synd-entry-actions');
    await expect(entry.locator('[data-note-edit]')).toContainText('수정');
    await entry.locator('[data-note-edit]').click();
    await expect(page.locator('#modal-note-compose-title')).toHaveText('노트 수정');
    await expect(page.locator('#note-compose-text')).toHaveValue('My note <img src=x onerror="window.__pwned = 1">');
    await expect(page.locator('#note-compose-language')).toHaveValue('en');
    await page.locator('#note-compose-text').fill('My note, changed');
    await page.locator('#btn-note-compose').click();
    expect(sent.at(-1)).toEqual({ method: 'PATCH', path: '/notes/900', body: { content: 'My note, changed', summary: null, language: 'en' } });

    // Delete: after the confirmation, the request, and the item goes from the list.
    await posts.nth(0).locator('[data-note-delete]').click();
    await expect(posts).toHaveCount(1);
    expect(sent.at(-1).method + ' ' + sent.at(-1).path).toBe('DELETE /notes/900');
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    expect(pageErrors).toEqual([]);
  });
});
