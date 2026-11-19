const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys, kbItem, contribution, installJsonApi } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// The public pages of public_kb.js: /kb, /kb/{number}/, /themes, and /themes/{number}/. The JSON answers have the shapes of api/server.py: "_collection_response" with "_kb_collection_item" (GET /kb), "themes_collection_route" (GET /themes), "_theme_view" (GET /themes/{number}/), "_contribution_view" (GET /kb/{number}/contributions), and "_member_view" (GET /kb/{number}/members).
// The E2E server runs in single mode without CLAIRE_MULTI_THEME, and its database has no theme. Thus "/kb/101/" (a shared knowledge base) and "/themes/202/" (a theme) get 404 from it. The spec gives the HTML page of "/kb" or "/themes" (from the server) with the bootstrap of the detail view ("render_public_kb_page": "view" and "ref"); the page script then does the same requests as on a server in network mode.

const ORIGIN = 'http://127.0.0.1:8766';

// The theme 202 with a shared knowledge base of this server, a shared knowledge base of the server b.example (no number, the origin link "url", and "remote_id"), and a public personal knowledge base of an account with the reputation level "limited".
const THEME_202 = { number: '202', url: ORIGIN + '/themes/202/', label: 'Dragons · Elves', description: 'Dragons, Elves', knowledge_type: 'fictional', state: 'active', top_entities: ['Dragon', 'Elf'], shared_knowledge_bases: 1, remote_knowledge_bases: 1, visible_contributions: 3, last_activity_at: 1700000000, knowledge_bases: [
  { number: '102', label: 'Fantasy Lore', is_personal: false, knowledge_type: 'fictional', url: ORIGIN + '/kb/102/', remote: false, server: null },
  { number: null, label: 'Elf Atlas', is_personal: false, knowledge_type: 'fictional', url: 'https://b.example/kb/77/', remote: true, server: 'b.example', remote_id: 4 },
  { number: '1000099', label: 'Carol Notes', is_personal: true, knowledge_type: null, url: null, remote: false, server: null, limited: true }
] };

const THEMES = [
  { number: '202', url: ORIGIN + '/themes/202', label: 'Dragons · Elves', description: 'Dragons, Elves', knowledge_type: 'fictional', state: 'active', knowledge_bases: 2, remote_knowledge_bases: 1 },
  { number: '201', url: ORIGIN + '/themes/201', label: 'Railway · Steam', description: '', knowledge_type: 'real', state: 'active', knowledge_bases: 1, remote_knowledge_bases: 0 },
  { number: '203', url: ORIGIN + '/themes/203', label: 'Canals', description: '', knowledge_type: 'real', state: 'merged', knowledge_bases: 0, remote_knowledge_bases: 0 }
];

function collection(path, key, items) {
  return { id: ORIGIN + path, type: 'OrderedCollection', totalItems: items.length, first: ORIGIN + path + '?page=true', [key]: items };
}

// Give the HTML page of "fromPath" for the navigation to "path", with the view "view" and the reference "ref" in the bootstrap.
async function servePublicView(page, path, fromPath, view, ref) {
  await page.route((u) => u.pathname === path, async (route) => {
    const req = route.request();
    if (req.resourceType() !== 'document') return route.fallback();
    const res = await route.fetch({ url: new URL(fromPath, req.url()).toString() });
    expect(res.status()).toBe(200);
    const html = (await res.text()).replace(/"view": "[a-z_]+", "ref": ""/, '"view": "' + view + '", "ref": "' + ref + '"');
    expect(html).toContain('"view": "' + view + '", "ref": "' + ref + '"');
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
  });
}

// The recent activity across servers that the root page has in its JSON element ("_recent_activity" with "knowledge_federation.recent"): the three kinds. An account that is not public has "account" null.
const RECENT = {
  recent: [
    { kind: 'curation', at: 1700000400, server: 'b.example', label: 'Elf Atlas', url: 'https://b.example/kb/77/', knowledge_base: { number: '101', label: 'Modern History', url: ORIGIN + '/kb/101/' } },
    { kind: 'accepted_contribution', at: 1700000300, server: 'b.example', label: 'Elf Atlas', url: 'https://b.example/kb/77/', account: { username: 'ben', display_name: 'Ben' } },
    { kind: 'accepted_contribution', at: 1700000200, server: 'b.example', label: 'Elf Atlas', url: 'https://b.example/kb/77/', account: null },
    { kind: 'remote_knowledge_base', at: 1700000100, server: 'b.example', label: 'Elf Atlas', url: 'https://b.example/kb/77/', knowledge_type: 'fictional' }
  ]
};

function installPublicApi(page, state) {
  return installJsonApi(page, (u) => u.pathname === '/kb' || u.pathname === '/themes' || /^\/(kb|themes)\/\d+\//.test(u.pathname), ({ path, method }) => {
    if (method !== 'GET') return null;
    if (path === '/kb') return [{ ...collection('/kb', 'knowledge_bases', state.kbs), personal_kb: null, multi_theme: true }];
    if (path === '/kb/101/') return [{ knowledge_base: state.kbs[0] }];
    if (path === '/kb/101/contributions') return [collection('/kb/101/contributions', 'contributions', state.contributions)];
    if (path === '/kb/101/members') return [collection('/kb/101/members', 'members', state.members)];
    if (path === '/themes') return [collection('/themes', 'themes', THEMES)];
    if (path === '/themes/202/') return [state.theme];
    return null;
  });
}

// The view of a contribution for a reader that is not a reviewer and not the contributor ("_contribution_view" with full false): no reason, no revisions, no removal at the origin, and no offer.
function publicView(c) {
  const { origin_removed, reason, revisions, offer, contributor_reputation, ...rest } = c;
  return rest;
}

function makeState(signedIn) {
  const state = {
    kbs: [
      kbItem(101, { label: 'Modern History', description: 'History of the 19th century', role: signedIn ? 'moderator' : null, membership_state: signedIn ? 'active' : null, stats: { documents: 30, entities: 120, relations: 80 } }),
      kbItem(102, { label: 'Fantasy Lore', description: 'Dragons and elves', knowledge_type: 'fictional', join_policy: 'open', state: 'archived', membership_state: signedIn ? 'pending' : null, reembed_required: true, stats: { documents: 4, entities: 9, relations: 2 } })
    ],
    // A reviewer (the admin) gets each state with the full view; an anonymous reader gets only the visible contributions.
    contributions: [
      contribution(9001, { document: 'copy-1', title: 'Railway Act 1844', contributor: { account: '5550001', display_name: 'Bob' } }),
      // A copy of a document of the server b.example by curation (federation design Section 10.1).
      contribution(9002, { document: 'copy-2', title: 'Rail Map 1850', origin: 'curation', contributor: { account: '1000001', display_name: 'E2E Admin' }, remote: { document: 'https://b.example/kb/901/documents/rail-map', knowledge_base: 'https://b.example/kb/901/', credit: 'https://b.example/users/dora' } }),
      // A contribution that an account of the server b.example offered (unit KF4): no local contributor, the credit is its actor.
      contribution(9003, { document: 'copy-3', title: 'Station List', contributor: null, remote: { document: 'https://b.example/@erin/documents/stations', knowledge_base: null, credit: 'https://b.example/users/erin' }, offer: { actor: 'https://b.example/users/erin', server: 'b.example', with_grant: false, grant_valid: false } }),
      ...(signedIn ? [contribution(9004, { document: 'copy-4', title: 'Hidden Draft', state: 'hidden', contributor: { account: '5550001', display_name: 'Bob' } })] : [])
    ],
    members: [
      { number: '101001', account: '1000001', display_name: 'E2E Admin', role: 'moderator', state: 'active', requested_at: 1700000000, decided_at: 1700000000 },
      { number: '101002', account: '5550001', display_name: 'Bob', role: 'member', state: 'active', requested_at: 1700000100, decided_at: 1700000200 },
      ...(signedIn ? [{ number: '101003', account: '5550002', display_name: 'Carol', role: 'member', state: 'pending', requested_at: 1700000300, decided_at: null }] : [])
    ]
  };
  if (!signedIn) state.contributions = state.contributions.map(publicView);
  // The admin (and a reviewer of a shared knowledge base of the theme) gets the follow state of this server for each remote member; another reader gets "follows" false and no follow state.
  state.theme = signedIn ? { ...THEME_202, follows: true, knowledge_bases: THEME_202.knowledge_bases.map((k) => (k.remote ? { ...k, follow: 'accepted' } : k)) } : { ...THEME_202, follows: false };
  return state;
}

test('/kb: the shared knowledge bases with type, state, join policy, and role; the type filter and the filter of joined ones', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbpublic1');
  const { calls, unknown } = await installPublicApi(page, makeState(true));
  await page.goto('/kb', { waitUntil: 'domcontentloaded' });
  const root = page.locator('#public-kb-root');
  await expect(root.locator('h1')).toContainText('공유 지식 베이스');
  await expect(root.locator('.claire-pubnav a[aria-current="page"]')).toHaveText('공유 지식 베이스');
  const rows = root.locator('tbody tr');
  await expect(rows).toHaveCount(2);
  // The first row: the link to the page of the knowledge base, the type, the join policy, the role, and the counts.
  await expect(rows.nth(0).locator('a[href="/kb/101/"]')).toHaveText('Modern History');
  await expect(rows.nth(0).locator('.claire-tag.acc')).toHaveText('현실');
  await expect(rows.nth(0)).toContainText('승인 필요');
  await expect(rows.nth(0).locator('.claire-tag.ok')).toHaveText('모더레이터');
  await expect(rows.nth(0)).toContainText('30 / 120');
  // The second row: fictional, archived, re-embedding necessary, and a join request that waits.
  await expect(rows.nth(1).locator('.claire-tag.fic')).toHaveText('허구');
  await expect(rows.nth(1)).toContainText('보관됨');
  await expect(rows.nth(1).locator('.claire-tag.danger')).toHaveText('재임베딩 필요');
  await expect(rows.nth(1)).toContainText('누구나 가입');
  await expect(rows.nth(1).locator('.claire-tag.warn').last()).toHaveText('가입 요청');
  await expect(root.locator('#pub-kb-make')).toHaveText(/공유 지식 베이스 만들기/);
  await expectNoRawKeys(page);

  // The type filter: only the fictional one; the address keeps the filter.
  await root.locator('.claire-chip', { hasText: '허구' }).click();
  await expect(rows).toHaveCount(1);
  await expect(rows.nth(0)).toContainText('Fantasy Lore');
  expect(new URL(page.url()).search).toBe('?knowledge_type=fictional');
  // All types, and only the knowledge bases with a role: a join request is not a role.
  await root.locator('.claire-chip', { hasText: '전체' }).click();
  await root.locator('#pub-kb-joined').check();
  await expect(rows).toHaveCount(1);
  await expect(rows.nth(0)).toContainText('Modern History');
  expect(calls.filter((c) => c.path === '/kb')).toHaveLength(1);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

test('/kb/101/ and the themes as the admin: the actions, the members, the contributions with a contributor of another server, and a theme with a knowledge base of another server', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbpublic2');
  const { unknown } = await installPublicApi(page, makeState(true));
  await servePublicView(page, '/kb/101/', '/kb', 'kb_detail', '101');
  await servePublicView(page, '/themes/202/', '/themes', 'theme_detail', '202');

  await page.goto('/kb/101/', { waitUntil: 'domcontentloaded' });
  const root = page.locator('#public-kb-root');
  await expect(root.locator('h1')).toContainText('Modern History');
  await expect(root.locator('h1 .claire-tag.ok')).toHaveText('모더레이터');
  await expect(root.locator('.claire-crumb a[href="/kb"]')).toBeVisible();
  // A moderator: direct contribution, leave, fork, and the link to the review page; no join control.
  await expect(root.locator('[data-kb-act="direct"]')).toBeVisible();
  await expect(root.locator('[data-kb-act="leave"]')).toHaveText('탈퇴');
  await expect(root.locator('[data-kb-act="fork"]')).toBeVisible();
  await expect(root.locator('[data-kb-act="join"]')).toHaveCount(0);
  await expect(root.locator('a[href="/settings/kb/101"]')).toHaveText('심사와 관리');
  await expect(root).toContainText('공유 지식 베이스에는 기여로만 문서를 넣습니다.');
  // The members: the role of an active member, the state of a join request.
  const members = root.locator('.claire-list-col .claire-list-item-row');
  await expect(members).toHaveCount(3);
  await expect(members.nth(0)).toContainText('E2E Admin');
  await expect(members.nth(0).locator('.claire-tag')).toHaveText('모더레이터');
  await expect(members.nth(2).locator('.claire-tag')).toHaveText('가입 요청');
  // The contributions: a visible one links to the document in the workspace; a hidden one has its state and no link.
  const docs = root.locator('.claire-detail-col tbody tr');
  await expect(docs).toHaveCount(4);
  await expect(docs.nth(0).locator('a[href="/kb/101/?doc=copy-1"]')).toHaveText('Railway Act 1844');
  await expect(docs.nth(0)).toContainText('Bob');
  await expect(docs.nth(0)).toContainText('직접 기여');
  await expect(docs.nth(1)).toContainText('E2E Admin');
  await expect(docs.nth(1)).toContainText('선별');
  // The contributor of another server shows as its actor, not as a deleted account.
  await expect(docs.nth(2)).toContainText('https://b.example/users/erin');
  await expect(docs.nth(2)).not.toContainText('삭제된 계정');
  await expect(docs.nth(3).locator('a')).toHaveCount(0);
  await expect(docs.nth(3).locator('.claire-tag.warn')).toHaveText('숨김');
  await expectNoRawKeys(page);

  // The themes: the active ones only, with the count of the knowledge bases of other servers.
  await page.goto('/themes', { waitUntil: 'domcontentloaded' });
  const cards = root.locator('article.claire-card');
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(0).locator('h2 a[href="/themes/202/"]')).toHaveText('Dragons · Elves');
  await expect(cards.nth(0).locator('.claire-tag.fic')).toHaveText('허구');
  await expect(cards.nth(0).locator('.claire-tags')).toHaveText('지식 베이스 2개다른 서버 1개');
  await expect(cards.nth(1).locator('.claire-tags .claire-tag')).toHaveCount(1);
  await root.locator('.claire-chip', { hasText: '현실' }).click();
  await expect(cards).toHaveCount(1);
  await expect(cards.nth(0)).toContainText('Railway · Steam');
  await expect(root.locator('#pub-th-create-kb')).toBeVisible();
  await expectNoRawKeys(page);

  // The theme 202: the counts, the shared knowledge base of this server with the fork, the one of b.example with the link to its origin and its server, and the personal one with the mark "limited".
  await page.goto('/themes/202/', { waitUntil: 'domcontentloaded' });
  await expect(root.locator('h1')).toContainText('Dragons · Elves');
  await expect(root.locator('.claire-page-sub').last()).toHaveText('이 서버의 공유 지식 베이스 1개, 다른 서버 1개, 기여 3건');
  await expect(root.locator('.claire-tags').first()).toHaveText('DragonElf');
  await expect(root.locator('#th-contrib-btn')).toContainText('이 테마에 기여');
  await expect(root.locator('#th-make-kb-btn')).toBeVisible();
  const shared = root.locator('section.claire-card').nth(1);
  await expect(shared.locator('tbody tr')).toHaveCount(1);
  await expect(shared.locator('a[href="/kb/102/"]')).toHaveText('Fantasy Lore');
  await expect(shared.locator('[data-fork-kb="102"]')).toBeVisible();
  const remote = root.locator('#th-remote-kbs');
  await expect(remote.locator('h2')).toHaveText('다른 서버의 공유 지식 베이스');
  await expect(remote.locator('tbody tr')).toHaveCount(1);
  const origin = remote.locator('a[href="https://b.example/kb/77/"]');
  await expect(origin).toHaveText('Elf Atlas');
  await expect(origin).toHaveAttribute('target', '_blank');
  await expect(origin).toHaveAttribute('rel', 'noopener noreferrer');
  await expect(remote.locator('tbody tr td').nth(1)).toHaveText('b.example');
  // The admin sees if this server follows the remote knowledge base (KNOWLEDGE_FEDERATION_DESIGN.md Section 9.2 rule 2).
  await expect(remote.locator('thead th').nth(2)).toHaveText('이 서버의 팔로우');
  await expect(remote.locator('[data-follow-state="accepted"]')).toHaveText('팔로우 중');
  // A knowledge base of another server has no page and no action on this server.
  await expect(remote.locator('a[href^="/kb/"], [data-fork-kb]')).toHaveCount(0);
  await expect(root.locator('a.claire-tag[href="/kb/1000099/"]')).toContainText('Carol Notes');
  await expect(root.locator('a.claire-tag[href="/kb/1000099/"] .claire-tag.warn')).toHaveText('제한');
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

test('the public pages without a sign-in: the link to the sign-in in place of the actions, and the link to the origin of a remote knowledge base', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  const { unknown } = await installPublicApi(page, makeState(false));
  await servePublicView(page, '/kb/101/', '/kb', 'kb_detail', '101');
  await servePublicView(page, '/themes/202/', '/themes', 'theme_detail', '202');

  await page.goto('/kb/101/', { waitUntil: 'domcontentloaded' });
  const root = page.locator('#public-kb-root');
  await expect(root.locator('h1')).toContainText('Modern History');
  await expect(root.locator('a.claire-btn[href="/login?next=%2Fkb%2F101%2F"]')).toHaveText('로그인하고 참여');
  await expect(root.locator('[data-kb-act]')).toHaveCount(0);
  await expect(root.locator('.claire-detail-col tbody tr')).toHaveCount(3);
  await expectNoRawKeys(page);

  await page.goto('/themes/202/', { waitUntil: 'domcontentloaded' });
  await expect(root.locator('h1')).toContainText('Dragons · Elves');
  await expect(root.locator('a.claire-btn[href="/login?next=%2Fthemes%2F202%2F"]')).toHaveText('로그인하고 참여');
  await expect(root.locator('#th-contrib-btn, #th-make-kb-btn, [data-fork-kb]')).toHaveCount(0);
  await expect(root.locator('#th-remote-kbs a[href="https://b.example/kb/77/"]')).toHaveAttribute('rel', 'noopener noreferrer');
  // A reader without a role gets no follow state.
  await expect(root.locator('#th-remote-kbs thead th')).toHaveCount(2);
  await expect(root.locator('[data-follow-state]')).toHaveCount(0);
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

test('the part of the root page about other servers: the three kinds of the recent activity across servers, and no list of the federated servers', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  const { unknown } = await installPublicApi(page, makeState(false));
  // The E2E server is in single mode and has no root page: the page of "/themes" gets the elements of the root page that public_kb.js reads ("public_directory_route" in network mode).
  await page.route((u) => u.pathname === '/themes', async (route) => {
    const req = route.request();
    if (req.resourceType() !== 'document') return route.fallback();
    const res = await route.fetch();
    const data = JSON.stringify(RECENT).replace(/</g, '\\u003c');
    const html = (await res.text()).replace('<main id="public-kb-root"', '<script type="application/json" id="public-home-recent-data">' + data + '</script><div id="public-home-recent"></div><main id="public-kb-root"');
    expect(html).toContain('id="public-home-recent"');
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: html });
  });

  await page.goto('/themes', { waitUntil: 'domcontentloaded' });
  const host = page.locator('#public-home-recent');
  await expect(host.locator('[data-federated-server]')).toHaveCount(0);
  const items = host.locator('[data-federation-recent]');
  await expect(items).toHaveCount(4);
  // A curation: the shared knowledge base of this server and the source on the other server.
  await expect(items.nth(0)).toHaveAttribute('data-federation-recent', 'curation');
  await expect(items.nth(0)).toContainText('다른 서버에서 가져온 문서:');
  await expect(items.nth(0).locator('a[href="/kb/101/"]')).toHaveText('Modern History');
  await expect(items.nth(0).locator('a[href="https://b.example/kb/77/"]')).toHaveAttribute('rel', 'noopener noreferrer');
  // An approval of another server: a public account has a link to its profile; an account that is not public is not named.
  await expect(items.nth(1)).toContainText('다른 서버가 승인한 기여:');
  await expect(items.nth(1).locator('a[href="/@ben/"]')).toHaveText('Ben');
  await expect(items.nth(2)).toHaveAttribute('data-federation-recent', 'accepted_contribution');
  await expect(items.nth(2).locator('a[href^="/@"]')).toHaveCount(0);
  await expect(items.nth(2)).not.toContainText('기여자:');
  await expect(items.nth(3)).toContainText('새 공유 지식 베이스:');
  await expect(items.nth(3)).toContainText('b.example');
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});
