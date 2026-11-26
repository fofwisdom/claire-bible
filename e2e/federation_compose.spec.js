// Compose controls of the federation client in /syndication (FEDERATION_REMOTE_READING_DESIGN.md Sections 5.3 and 5.4, unit FP4-9), and the quote controls (FEP-044f, Sections 5.4 rules 5 and 6 and 6.6 rule 5, unit FP4-9Q).
// The server of this test directory is in single mode. The federation routes work in both server modes (KNOWLEDGE_FEDERATION_DESIGN.md Section 15 rule 5); this file answers them itself, as federation_client.spec.js does, so that the test needs no remote server. The routes have their own end-to-end tests with a real app in network mode: tests/test_e2e_federation_outbound.py and tests/test_e2e_federation_quotes.py.
const { test, expect } = require('@playwright/test');

const NOW = Math.floor(Date.now() / 1000);
const carol = { iri: 'https://remote.example/users/carol', name: 'Carol', handle: '@carol@remote.example' };
const me = { iri: 'http://127.0.0.1:8766/ap/actors/1', name: 'Owner', handle: '@owner@127.0.0.1:8766' };

function item(id, changes) {
  return Object.assign({
    id: String(id), object_iri: 'https://remote.example/notes/' + id, url: 'https://remote.example/@carol/' + id, type: 'Note', author: carol, announced_by: null, name: null, summary: null, sensitive: false,
    text: 'Plain text of post ' + id, language: 'en', attachments: 0, in_reply_to: null, in_timeline: true, published_at: NOW - 600, updated_at: null, received_at: NOW - 500, unread: false, starred: false, mention: false, linked: null,
    favourited: false, boosted: false, note: null, quote: null,
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
    // An own note with a script in its text, and a post of a remote actor.
    const own = item(8, { object_iri: 'http://127.0.0.1:8766/ap/objects/900', author: me, text: 'My note <img src=x onerror="window.__pwned = 1">', note: { id: '900', content: 'My note <img src=x onerror="window.__pwned = 1">', summary: null, sensitive: false, language: 'en', edited_at: NOW - 60, likes: 2, announces: 1, quotes: 0, quote_policy: 'public', quote: null } });
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
    await page.route('**/api/federation/status', route => route.fulfill(json({ handle: me.handle, actor_id: me.iri, is_actor: true, actor_state: 'active', discoverable: true, followers: { total: 0 }, pending_tasks: 0 })));
    await page.route('**/api/federation/objects?**', route => route.fulfill(json({ total: 0, items: [], next_max_id: null })));
    await page.route('**/api/federation/quotes', route => route.fulfill(json({ requests: [], authorizations: [], counts: { pending: 0, approved: 0 } })));

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
    await expect(mine.locator('.synd-note-counters')).toHaveText('좋아요 2 · 부스트 1 · 인용 0 · 수정됨');
    // Without a quote request, the tree has no folder of quote requests.
    await expect(page.locator('#client-quotes-folder')).toBeHidden();
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
    expect(sent.at(-1)).toEqual({ method: 'POST', path: '/notes', body: { content: '@carol@remote.example Thank you #Claire', summary: 'A warning', language: 'ko', quote_policy: 'public', in_reply_to: '7' } });

    // New note: no reply target and no handle.
    await page.locator('#btn-note-new').click();
    await expect(page.locator('#modal-note-compose-title')).toHaveText('새 노트');
    await expect(page.locator('#note-compose-text')).toHaveValue('');
    await expect(page.locator('#note-compose-context')).toBeHidden();
    await page.locator('#note-compose-text').fill('Hello');
    await page.locator('#btn-note-compose').click();
    expect(sent.at(-1)).toEqual({ method: 'POST', path: '/notes', body: { content: 'Hello', quote_policy: 'public' } });

    // Edit: the dialog has the plain text of the note. The reader has the same controls with their text.
    await posts.nth(0).locator('.synd-post-text').click();
    const entry = page.locator('#client-reader .synd-entry-actions');
    await expect(entry.locator('[data-note-edit]')).toContainText('수정');
    await entry.locator('[data-note-edit]').click();
    await expect(page.locator('#modal-note-compose-title')).toHaveText('노트 수정');
    await expect(page.locator('#note-compose-text')).toHaveValue('My note <img src=x onerror="window.__pwned = 1">');
    await expect(page.locator('#note-compose-language')).toHaveValue('en');
    await page.locator('#note-compose-text').fill('My note, changed');
    await page.locator('#btn-note-compose').click();
    expect(sent.at(-1)).toEqual({ method: 'PATCH', path: '/notes/900', body: { content: 'My note, changed', summary: null, language: 'en', quote_policy: 'public' } });

    // Delete: after the confirmation, the request, and the item goes from the list.
    await posts.nth(0).locator('[data-note-delete]').click();
    await expect(posts).toHaveCount(1);
    expect(sent.at(-1).method + ' ' + sent.at(-1).path).toBe('DELETE /notes/900');
    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    expect(pageErrors).toEqual([]);
  });

  test('FP4-9Q: quote of an item, quote state of a note, quote requests with approve, refuse, and revoke, and the quote policy of a note and of a published document', async ({ page }) => {
    const pageErrors = [];
    const sent = [];
    page.on('pageerror', error => { if (!error.message.includes('Service worker is disabled because the context is sandboxed')) pageErrors.push(error.message); });
    page.on('dialog', dialog => dialog.accept());
    await page.setViewportSize({ width: 1400, height: 900 });
    const json = (body, status) => ({ status: status || 200, contentType: 'application/json', body: JSON.stringify(body) });
    // An own note whose quote waits for the author, a post of a remote actor that quotes item 7, and item 7.
    const own = item(8, { object_iri: 'http://127.0.0.1:8766/ap/objects/900', author: me, text: 'My quote', quote: { iri: 'https://remote.example/notes/5', item_id: null, state: 'pending' }, note: { id: '900', content: 'My quote', summary: null, sensitive: false, language: 'en', edited_at: null, likes: 0, announces: 0, quotes: 3, quote_policy: 'followers', quote: { iri: 'https://remote.example/notes/5', state: 'pending' } } });
    const items = [own, item(6, { quote: { iri: 'https://remote.example/notes/7', item_id: '7', state: null } }), item(7)];
    const counts = { total: 3, unread: 0, starred: 0, mentions: 0, unread_mentions: 0, ingested: 0, published: 0, private_messages: 0 };
    // One request that waits, from an actor whose address has markup characters, and one approved request.
    const evil = 'https://remote.example/users/<img src=x onerror="window.__pwned = 1">';
    const quotes = {
      requests: [{ id: '31', state: 'pending', object: { id: '900', url: 'http://127.0.0.1:8766/ap/objects/900', kind: 'note', text: 'My <b>note</b>' }, quoting_object: 'https://remote.example/notes/31', quoting_actor: evil, authorization: null, created_at: NOW, updated_at: NOW },
        { id: '33', state: 'pending', object: { id: '901', url: 'http://127.0.0.1:8766/ap/objects/901', kind: 'note', text: 'A second note' }, quoting_object: 'javascript:alert(1)', quoting_actor: 'https://remote.example/users/dan', authorization: null, created_at: NOW, updated_at: NOW }],
      authorizations: [{ id: '32', state: 'approved', object: { id: '900', url: 'http://127.0.0.1:8766/ap/objects/900', kind: 'gone', text: '' }, quoting_object: 'https://remote.example/notes/32', quoting_actor: 'https://remote.example/users/carol', authorization: 'http://127.0.0.1:8766/ap/quote-authorizations/32', created_at: NOW, updated_at: NOW }, { id: '34', state: 'approved', object: { id: '950', url: 'http://127.0.0.1:8766/ap/objects/950', kind: 'document', text: 'A document', kb_id: 1, document_id: 'doc-1' }, quoting_object: 'https://remote.example/notes/34', quoting_actor: 'https://remote.example/users/erin', authorization: 'http://127.0.0.1:8766/ap/quote-authorizations/34', created_at: NOW, updated_at: NOW }],
    };
    const published = { items: [] };
    await page.route('**/api/federation/timeline?**', route => route.fulfill(json({ items, next_max_id: null, counts })));
    await page.route('**/api/federation/timeline/*', route => {
      const found = items.find(entry => entry.id === route.request().url().split('/').pop());
      const detail = Object.assign({}, found, { render_url: '/render/remote/' + found.id + '?t=test' });
      return route.fulfill(json({ item: detail, thread: [detail] }));
    });
    await page.route('**/api/federation/timeline/*/context', route => route.fulfill(json({ state: 'off', pending: false })));
    await page.route('**/api/federation/notes', route => {
      sent.push({ method: 'POST', path: '/notes', body: route.request().postDataJSON() });
      return route.fulfill(json({ note: { id: '902' }, item_id: '10' }, 201));
    });
    await page.route('**/api/federation/notes/*', route => {
      sent.push({ method: route.request().method(), path: '/notes/' + route.request().url().split('/').pop(), body: route.request().postDataJSON() });
      return route.fulfill(json({ note: Object.assign({}, own.note, route.request().postDataJSON()) }));
    });
    await page.route('**/api/federation/quotes', route => route.fulfill(json({ requests: quotes.requests, authorizations: quotes.authorizations, counts: { pending: quotes.requests.length, approved: quotes.authorizations.length } })));
    await page.route('**/api/federation/quotes/*', route => {
      const id = route.request().url().split('/').pop();
      const state = route.request().postDataJSON().state;
      sent.push({ method: 'PATCH', path: '/quotes/' + id, body: route.request().postDataJSON() });
      const entry = quotes.requests.concat(quotes.authorizations).find(q => q.id === id);
      quotes.requests = quotes.requests.filter(q => q.id !== id);
      quotes.authorizations = quotes.authorizations.filter(q => q.id !== id);
      if (state === 'approved') quotes.authorizations.push(Object.assign(entry, { state }));
      return route.fulfill(json({ quote: Object.assign({}, entry, { state }) }));
    });
    await page.route('**/api/federation/objects?**', route => route.fulfill(json({ total: published.items.length, items: published.items, next_max_id: null })));
    await page.route('**/render/remote/**', route => route.fulfill({ status: 200, contentType: 'text/html', body: '<!doctype html><html><body><p>Body</p></body></html>' }));
    await page.route('**/api/federation/following?**', route => route.fulfill(json({ total: 0, limit: 1000, items: [], next_max_id: null })));
    await page.route('**/api/federation/status', route => route.fulfill(json({ handle: me.handle, actor_id: me.iri, is_actor: true, actor_state: 'active', discoverable: true, followers: { total: 0 }, pending_tasks: 0 })));

    await page.goto('/?t=e2e-fedquote-token-0123456789abcde', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    await page.goto('/syndication?stage=collect', { waitUntil: 'domcontentloaded' });
    const posts = page.locator('.synd-post');
    await expect(posts).toHaveCount(3);

    // A post of a remote actor has the control "Quote" (a line icon with a name). An own note has no quote of its own account, and shows the number of its quotes.
    const remote = posts.nth(2);
    await expect(remote.locator('[data-note-quote] svg')).toHaveCount(1);
    await expect(remote.locator('[data-note-quote]')).toHaveAttribute('aria-label', '인용');
    await expect(posts.nth(0).locator('[data-note-quote]')).toHaveCount(0);
    await expect(posts.nth(0).locator('.synd-note-counters')).toHaveText('좋아요 0 · 부스트 0 · 인용 3');
    // A post that quotes has the badge.
    await expect(posts.nth(1).locator('.synd-post-meta')).toContainText('인용');
    await expect(posts.nth(2).locator('.synd-post-meta')).not.toContainText('인용');

    // Quote: the dialog names the author, has no handle in the text, and sends the item and the quote policy.
    await remote.locator('[data-note-quote]').click();
    const dialog = page.locator('#modal-note-compose');
    await expect(dialog).toHaveAttribute('open', '');
    await expect(page.locator('#modal-note-compose-title')).toHaveText('인용');
    await expect(page.locator('#note-compose-context')).toHaveText('Carol의 글 인용');
    await expect(page.locator('#note-compose-text')).toHaveValue('');
    await expect(page.locator('#note-compose-quote-policy')).toHaveValue('public');
    await expect(page.locator('#note-compose-quote-policy option')).toHaveText(['모두 (자동 승인)', '팔로워만', '요청마다 직접 승인', '아무도 안 됨']);
    await page.locator('#note-compose-text').fill('Look at this');
    await page.locator('#note-compose-quote-policy').selectOption('manual');
    await page.screenshot({ path: 'test-results/federation-quote.png' });
    await page.locator('#btn-note-compose').click();
    await expect(dialog).not.toHaveAttribute('open', '');
    expect(sent.at(-1)).toEqual({ method: 'POST', path: '/notes', body: { content: 'Look at this', quote_policy: 'manual', quote_of: '7' } });

    // The reader of an own note shows the quote state and a link to the quoted post on its server: the note has no item for it.
    await posts.nth(0).locator('.synd-post-text').click();
    const state = page.locator('#client-reader .synd-quote');
    await expect(state).toHaveAttribute('data-quote-state', 'pending');
    await expect(state.locator('.synd-quote-state')).toHaveText('작성자가 아직 인용을 승인하지 않았습니다. 독자에게는 링크가 보입니다.');
    await expect(state.locator('a')).toHaveAttribute('href', 'https://remote.example/notes/5');
    await expect(state.locator('a')).toHaveAttribute('rel', 'noopener noreferrer nofollow');
    // Edit of the own note: the dialog has the quote policy of the note, and the request sends the new one.
    await page.locator('#client-reader [data-note-edit]').click();
    await expect(page.locator('#note-compose-quote-policy')).toHaveValue('followers');
    await page.locator('#note-compose-quote-policy').selectOption('nobody');
    await page.locator('#btn-note-compose').click();
    expect(sent.at(-1)).toEqual({ method: 'PATCH', path: '/notes/900', body: { content: 'My quote', summary: null, language: 'en', quote_policy: 'nobody' } });

    // A post of a remote actor that quotes an item of the timeline: the control opens that item, and Claire shows no state for it.
    await posts.nth(1).locator('.synd-post-text').click();
    const quoted = page.locator('#client-reader .synd-quote');
    await expect(quoted).not.toHaveAttribute('data-quote-state', /.+/);
    await quoted.locator('[data-quote-open="7"]').click();
    await expect(page.locator('.synd-post.is-selected')).toHaveAttribute('data-item-id', '7');
    await expect(page.locator('#client-reader .synd-quote')).toHaveCount(0);

    // Quote requests in the tree: the number of requests that wait, the text as characters, and the controls.
    const folder = page.locator('#client-quotes-folder');
    await expect(folder).toBeVisible();
    await expect(page.locator('#client-quotes-count')).toHaveText('2');
    const requests = page.locator('#client-quotes .synd-quote-request');
    await expect(requests).toHaveCount(4);
    await expect(requests.nth(0)).toContainText('<img src=x onerror=');
    await expect(requests.nth(0)).toContainText('My <b>note</b>');
    await expect(page.locator('#client-quotes img, #client-quotes b')).toHaveCount(0);
    await expect(requests.nth(0).locator('a')).toHaveAttribute('href', 'https://remote.example/notes/31');
    // An address that is not HTTP is not a link.
    await expect(requests.nth(1).locator('a')).toHaveCount(0);
    await expect(requests.nth(0).locator('[data-quote-decision="approved"] svg, [data-quote-decision="refused"] svg')).toHaveCount(2);
    await expect(requests.nth(2)).toContainText('(없어진 글)');
    await expect(requests.nth(2).locator('[data-quote-decision]')).toHaveText(['철회']);
    // A quote of a published document is always permitted: its row has no control to refuse or revoke.
    await expect(requests.nth(3)).toContainText('A document');
    await expect(requests.nth(3).locator('[data-quote-decision]')).toHaveCount(0);

    // Approve, refuse, and revoke send the decision of the owner.
    await requests.nth(0).locator('[data-quote-decision="approved"]').click();
    await expect(page.locator('#client-quotes-count')).toHaveText('1');
    await page.locator('[data-quote-request="33"] [data-quote-decision="refused"]').click();
    await expect(page.locator('[data-quote-request="33"]')).toHaveCount(0);
    await expect(page.locator('#client-quotes-count')).toHaveText('');
    await page.locator('[data-quote-request="32"] [data-quote-decision="revoked"]').click();
    await expect(page.locator('[data-quote-request="32"]')).toHaveCount(0);
    await expect(page.locator('#client-quotes .synd-quote-request')).toHaveCount(2);
    expect(sent.slice(-3)).toEqual([
      { method: 'PATCH', path: '/quotes/31', body: { state: 'approved' } },
      { method: 'PATCH', path: '/quotes/33', body: { state: 'refused' } },
      { method: 'PATCH', path: '/quotes/32', body: { state: 'revoked' } },
    ]);

    // A published document has no quote policy: the context menu of the knowledge base view has no such command, and the row shows the number of quotes.
    await page.locator('[data-stage-btn="ingest"]').click();
    const row = page.locator('#headlines-container [data-row-id]').first();
    await expect(row).toBeVisible();
    const key = await page.evaluate(() => { const theme = window.ClaireSyndication.curTheme(); return { kb_id: theme.id, document_id: document.querySelector('#headlines-container [data-row-id]').getAttribute('data-row-id') }; });
    published.items = [Object.assign({ id: '950', published_at: NOW, updated_at: NOW, likes: 0, announces: 0, quotes: 2, plus_ones: [{ template: 'standard', score: 0 }, { template: 'concise', score: 3 }] }, key)];
    await page.goto('/syndication?stage=ingest', { waitUntil: 'domcontentloaded' });
    const publishedRow = page.locator(`#headlines-container [data-row-id="${key.document_id}"]`);
    await expect(publishedRow.locator('[data-reactions]')).toContainText('인용 2');
    // The +1 score of each rendition with a score (DOCUMENT_RENDITIONS_AND_PUBLICATION_DESIGN.md Section 5.12 rule 13).
    await expect(publishedRow.locator('[data-reactions]')).toContainText('+1 간결 3');
    await publishedRow.click({ button: 'right' });
    await expect(page.locator('#pop-doc-context')).toBeVisible();
    await expect(page.locator('#menu-doc-quote-policy, #modal-quote-policy')).toHaveCount(0);
    await page.keyboard.press('Escape');

    expect(await page.evaluate(() => window.__pwned)).toBeUndefined();
    expect(pageErrors).toEqual([]);
  });
});
