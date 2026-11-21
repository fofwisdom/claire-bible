const { expect } = require('@playwright/test');

// Shared functions of the specs for the shared knowledge base pages and the federation admin pages.

// Sign in as the owner with a single-use token of e2e/seed_db.py ("e2e-<name>-token-0123456789abc").
async function authenticateOwner(page, name) {
  // db.plausible_session_token refuses a token shorter than 32 characters: the name must have 9 characters or more.
  expect(name.length, 'token name too short').toBeGreaterThanOrEqual(9);
  await page.goto('/?t=e2e-' + name + '-token-0123456789abc', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
}

function json(route, body, status) {
  return route.fulfill({ status: status || 200, contentType: 'application/json', body: JSON.stringify(body) });
}

// Record each JavaScript error of the page: an exception that no code caught, and each console message of the type "error" that is not a failed resource load.
function watchErrors(page) {
  const errors = [];
  page.on('pageerror', (err) => {
    if (String(err && err.message || '').includes('ViewTransition opt-in disabled')) return;
    errors.push('pageerror: ' + err.message);
  });
  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    const text = msg.text();
    // The browser writes one line for each answer with an error status. The tests give such answers on purpose.
    if (text.startsWith('Failed to load resource')) return;
    errors.push('console: ' + text);
  });
  return errors;
}

// A catalog key that shows as text: "console.x.y", "kb.x.y", and the other key domains. A host name such as "b.example" has two parts only and does not match.
const RAW_KEY = /(?:^|[\s"'(>:])((?:console|kb|common|ws|synd|reader|explore)\.[a-z0-9_]+(?:\.[a-z0-9_]+)+)/;

// Fail when the visible text, a title, a placeholder, or an accessible name of the page has a raw catalog key.
async function expectNoRawKeys(page, scope) {
  const texts = await page.evaluate((selector) => {
    const root = selector ? document.querySelector(selector) : document.body;
    if (!root) return [];
    const out = [root.innerText || ''];
    document.querySelectorAll('dialog[open], .claire-toast').forEach((el) => out.push(el.innerText || ''));
    root.querySelectorAll('[title], [placeholder], [aria-label]').forEach((el) => {
      ['title', 'placeholder', 'aria-label'].forEach((name) => { if (el.hasAttribute(name)) out.push(el.getAttribute(name)); });
    });
    return out;
  }, scope || null);
  for (const text of texts) {
    const found = RAW_KEY.exec(text);
    expect(found ? found[1] : null, 'raw catalog key in the page text').toBeNull();
  }
}

// Record each JSON request that the route function gets: method, path with the query, and the parsed body.
function recordCall(calls, req) {
  const url = new URL(req.url());
  let body = null;
  if (req.postData()) {
    try { body = JSON.parse(req.postData()); } catch (_err) { body = req.postData(); }
  }
  calls.push({ method: req.method(), path: url.pathname + url.search, body });
}

// One knowledge base in the shape of "KnowledgeBaseInfo.to_dict()" (store/knowledge_base.py): the answer of a fork, and the base of a collection item.
function kbInfo(number, over) {
  const n = Number(number);
  return { id: n, seq: n, label: 'KB ' + number, description: '', icon: '📚', db_path: '', is_personal: false, is_public: true, fqdn: '', ga_measurement_id: '', sorcerer: '', presentation_enabled: false, created_at: 1700000000, updated_at: 1700000000, account_id: null, created_by_account_id: 1, knowledge_type: 'real', join_policy: 'approval', state: 'active', archived_at: null, seed_entities: [], number: String(number), visibility_epoch: 1, embed_provider: 'gemini', embed_model: 'gemini-embedding-001', knowledge_manager: '', ...over };
}

// One knowledge base in the shape of "_kb_collection_item" (api/server.py) for a requester with the capability "settings" (the settings view keeps the internal fields).
function kbItem(number, over) {
  return { ...kbInfo(number), sorcerer: '', effective_sorcerer: 'owner', knowledge_manager: 'owner', stats: { documents: 0, entities: 0, relations: 0 }, url: 'http://127.0.0.1:8766/kb/' + number + '/', role: null, membership_state: null, legacy_hosts: [], reembed_required: false, active_moderators: 1, ...over };
}

// One contribution in the shape of "_contribution_view" (api/server.py) with "full" and with the help for a reviewer.
function contribution(number, over) {
  return { number: String(number), document: 'copy-' + number, title: 'Document ' + number, origin: 'direct', state: 'visible', contributor: { account: '5550001', display_name: 'Bob' }, approved: true, approved_at: 1700000200, updates_stopped: false, contributed_at: 1700000000, revised_at: null, remote: null, origin_removed: null, reason: '', revisions: [], offer: null, contributor_reputation: { level: 'good', approval_rate: 0.9 }, ...over };
}

// Install the fixed answers of the JSON routes. "handler(ctx)" gives [body] or [body, status] for a request that it knows, and nothing for each other request (which gets 404 "not found" and a record in "unknown"). A page navigation always goes to the server.
async function installJsonApi(page, matcher, handler) {
  const calls = [];
  const unknown = [];
  await page.route(matcher, async (route) => {
    const req = route.request();
    if (req.resourceType() === 'document') return route.fallback();
    const url = new URL(req.url());
    const ctx = { req, url, path: url.pathname, method: req.method(), query: url.searchParams, body: null, route };
    if (req.postData()) {
      try { ctx.body = JSON.parse(req.postData()); } catch (_err) { ctx.body = req.postData(); }
    }
    const answer = await handler(ctx);
    if (answer === 'fallback') return route.fallback();
    calls.push({ method: ctx.method, path: url.pathname + url.search, body: ctx.body });
    if (!answer) {
      unknown.push(ctx.method + ' ' + url.pathname + url.search);
      return json(route, { error: 'not found' }, 404);
    }
    return json(route, answer[0], answer[1]);
  });
  return { calls, unknown };
}

module.exports = { kbInfo, kbItem, contribution, installJsonApi, authenticateOwner, json, watchErrors, expectNoRawKeys, recordCall };
