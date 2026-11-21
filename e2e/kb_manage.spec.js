const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys, kbItem, contribution, installJsonApi } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// /moderation/kb/101 (the review and manage page of one shared knowledge base) against answers in the shapes of api/server.py: "_kb_collection_item", "_contribution_view", "_member_view", "_theme_view", "kb_remote_sources_route", "_remote_read_view", "kb_curations_route", "_curate_remote", and "kb_moderation_log_route". The seed database has the shared knowledge base 101 with the admin as its moderator; the page itself comes from the E2E server.

const REMOTE_DOC_1 = 'https://b.example/kb/901/documents/rail-map';
const REMOTE_DOC_2 = 'https://b.example/kb/901/documents/station-list';

function makeState(over) {
  return {
    kb: kbItem(101, { label: 'Modern History', description: 'History of the 19th century', seed_entities: ['Railway'], role: 'moderator', membership_state: 'active', stats: { documents: 30, entities: 120, relations: 80 } }),
    contributions: [],
    members: [
      { number: '101001', account: '1000001', display_name: 'E2E Admin', role: 'moderator', state: 'active', requested_at: 1700000000, decided_at: 1700000000 },
      { number: '101002', account: '5550001', display_name: 'Bob', role: 'member', state: 'active', requested_at: 1700000100, decided_at: 1700000200 }
    ],
    log: [],
    remoteReads: [
      { read: 7, document: 'https://b.example/kb/901/documents/old-bridge', title: 'Old Bridge', state: 'done', error: null, contribution: '9020', requested_at: 1700000300 },
      { read: 8, document: 'https://b.example/kb/901/documents/gone', title: null, state: 'failed', error: 'remote_document_gone', contribution: null, requested_at: 1700000400 }
    ],
    listing: { state: 'ready', error: null, read_at: 1700000500 },
    ...over
  };
}

async function installApi(page, state) {
  const themes = [{ number: '201', url: 'http://127.0.0.1:8766/themes/201', label: 'Railway · Steam', description: 'Railway, Steam', knowledge_type: 'real', state: 'active', knowledge_bases: 2, remote_knowledge_bases: 1 }];
  const remoteSource = { id: 5, iri: 'https://b.example/kb/901/', server: 'b.example', url: 'https://b.example/kb/901/', label: 'Railway Archive', description: 'Rail records', knowledge_type: 'real', documents: 12, entities: 40, relations: 22, first_seen_at: 1700000000, follow: 'accepted' };
  return installJsonApi(page, (u) => u.pathname === '/kb' || u.pathname === '/themes' || /^\/(kb|themes)\/\d+\//.test(u.pathname), ({ path, method, query, body }) => {
    if (path === '/kb' && method === 'GET') return [{ type: 'OrderedCollection', totalItems: 1, knowledge_bases: [state.kb], personal_kb: null, multi_theme: true }];
    if (path === '/kb/101/' && method === 'GET') return [{ knowledge_base: state.kb }];
    if (path === '/kb/101/' && method === 'PATCH') {
      Object.assign(state.kb, body);
      return [{ ok: true, knowledge_base: state.kb }];
    }
    if (path === '/kb/101/contributions' && method === 'GET') return [{ type: 'OrderedCollection', totalItems: state.contributions.length, contributions: state.contributions }];
    if (path === '/kb/101/members') return [{ type: 'OrderedCollection', totalItems: state.members.length, members: state.members }];
    if (path === '/kb/101/moderation-log') return [{ type: 'OrderedCollection', totalItems: state.log.length, log: state.log }];
    // The collaborators of other servers (unit KF4, "kb_remote_collaborators_route"): {"grants": [...]}. This spec does not test them; the list is empty.
    if (path === '/kb/101/remote-collaborators' && method === 'GET') return [{ grants: [] }];
    if (path === '/themes') return [{ type: 'OrderedCollection', totalItems: 1, themes }];
    // GET /themes/201/ as JSON: "_theme_view". The members: this knowledge base, one other shared knowledge base of this server, and one of the server b.example (it has no number).
    if (path === '/themes/201/') return [{ number: '201', url: 'http://127.0.0.1:8766/themes/201/', label: 'Railway · Steam', description: 'Railway, Steam', knowledge_type: 'real', state: 'active', top_entities: ['Railway', 'Steam'], shared_knowledge_bases: 2, remote_knowledge_bases: 1, visible_contributions: 3, last_activity_at: 1700000000, knowledge_bases: [
      { number: '101', label: 'Modern History', is_personal: false, knowledge_type: 'real', url: 'http://127.0.0.1:8766/kb/101/', remote: false, server: null },
      { number: '104', label: 'Steam Engines', is_personal: false, knowledge_type: 'real', url: 'http://127.0.0.1:8766/kb/104/', remote: false, server: null },
      { number: null, label: 'Railway Archive', is_personal: false, knowledge_type: 'real', url: 'https://b.example/kb/901/', remote: true, server: 'b.example', remote_id: 5 }
    ] }];
    if (path === '/kb/104/documents') return [{ documents: [{ id: 'eng-1', title: 'Boiler Design', hidden: 0 }, { id: 'eng-2', title: 'Valve Gear', hidden: 0 }, { id: 'eng-3', title: 'Hidden Draft', hidden: 1 }], next_before: null, total: 3 }];
    if (path === '/kb/101/remote-sources' && method === 'GET') {
      const answer = { sources: [remoteSource], curations: state.remoteReads };
      if (query.get('source') === '5') answer.selected = { id: 5, ...state.listing, documents: state.listing.state === 'ready' ? [
        { iri: REMOTE_DOC_1, title: 'Rail Map 1850', url: 'https://b.example/kb/901/?doc=rail-map', published: '2026-11-01T00:00:00Z', updated: null, curated: null },
        { iri: REMOTE_DOC_2, title: 'Station List', url: 'https://b.example/kb/901/?doc=station-list', published: '2026-10-01T00:00:00Z', updated: null, curated: 'visible' }
      ] : [] };
      return [answer];
    }
    if (path === '/kb/101/curations' && method === 'POST') {
      if (body.remote_source !== undefined) {
        state.remoteReads = [{ read: 9, document: body.documents[0], title: null, state: 'queued', error: null, contribution: null, requested_at: 1700000600 }].concat(state.remoteReads);
        return [{ ok: true, curations: body.documents.map((document, i) => ({ document, read: 9 + i, state: 'queued' })) }, 202];
      }
      if (body.documents.includes('eng-2')) return [{ ok: true, curations: body.documents.map((document) => (document === 'eng-2' ? { document, error: 'contribution_duplicate' } : { document, contribution: contribution(9030, { document: 'copy-eng', title: 'Boiler Design', origin: 'curation' }) })) }, 201];
      state.contributions = state.contributions.concat([contribution(9030, { document: 'copy-eng', title: 'Boiler Design', origin: 'curation', contributor: { account: '1000001', display_name: 'E2E Admin' } })]);
      return [{ ok: true, curations: body.documents.map((document) => ({ document, contribution: state.contributions[state.contributions.length - 1] })) }, 201];
    }
    const review = /^\/kb\/101\/contributions\/(\d+)$/.exec(path);
    if (review && method === 'PATCH') {
      const row = state.contributions.find((c) => c.number === review[1]);
      if (state.kb.state === 'archived') return [{ error: 'kb_archived' }, 403];
      if (body.action === 'unhide') Object.assign(row, { state: 'visible', origin_removed: row.origin_removed ? { ...row.origin_removed, waiting: false } : null });
      if (body.action === 'hide') row.state = 'hidden';
      if (body.action === 'approve') Object.assign(row, { state: 'visible', approved: true });
      if (body.action === 'reject') Object.assign(row, { state: 'rejected', reason: body.reason });
      return [{ ok: true, contribution: row }];
    }
    // The data bundle of the knowledge base ("create_graph_bundle_route", "GraphBundleInfo.to_dict"): a moderator or the admin makes it; the download address has the token.
    if (path === '/kb/101/graph/bundle' && method === 'POST') return [{ bundle_id: 'gb_20261118_000000_e2e00001', download_url: 'http://127.0.0.1:8766/graph/bundle?token=gb3_e2eBundleToken', token: 'gb3_e2eBundleToken', filename: 'graph_bundle_gb_20261118_000000_e2e00001.tar.zst', kb_id: 101, entities_count: 120, relations_count: 80, documents_count: 30, contributions_count: 28, shared: true, size_bytes: 4096, created_at: '2026-11-18T00:00:00+00:00', expires_at: '2026-11-19T00:00:00+00:00', review: {} }];
    const remove = /^\/kb\/101\/contributions\/(\d+)\/remove$/.exec(path);
    if (remove && method === 'POST') {
      state.contributions = state.contributions.map((c) => (c.number === remove[1] ? { ...c, state: 'removed', reason: body.reason, origin_removed: c.origin_removed ? { ...c.origin_removed, waiting: false } : null } : c));
      return [{ ok: true }];
    }
    return null;
  });
}

function tab(page, id) {
  return page.locator('#kb-manage-tabs [role="tab"][data-tab="' + id + '"]');
}

test('/moderation/kb/101 curation: a source of this server and a source of another server, the copy requests and their results', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbmanage1');
  const state = makeState();
  const { calls, unknown } = await installApi(page, state);
  await page.goto('/moderation/kb/101', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#kb-manage-tabs [role="tab"]')).toHaveText([/심사 대기열/, '기여', '선별', '구성원과 역할', '설정', '모더레이션 기록']);
  await tab(page, 'curate').click();
  const host = page.locator('#kb-curate-host');
  const sources = host.locator('[data-curate-src]');
  // The sources: the other knowledge base of the theme on this server, then the one of the server b.example with the follow state.
  await expect(sources).toHaveCount(2);
  await expect(sources.nth(0)).toContainText('Steam Engines');
  await expect(sources.nth(0)).toHaveClass(/active/);
  await expect(sources.nth(1)).toContainText('Railway Archive');
  await expect(sources.nth(1)).toContainText('다른 서버: b.example');
  await expect(sources.nth(1)).toContainText('팔로우 중');
  expect(calls.some((c) => c.method === 'GET' && c.path === '/kb/101/remote-sources')).toBe(true);
  await expectNoRawKeys(page);

  // The source of this server: the visible documents; a hidden document is not in the list.
  const docs = host.locator('#kb-curate-docs');
  await expect(docs.locator('input[name="doc"]')).toHaveCount(2);
  await expect(docs).toContainText('Boiler Design');
  await expect(docs).not.toContainText('Hidden Draft');
  // No document is selected: no request.
  await docs.locator('button[type="submit"]').click();
  // Two documents, one of them is a duplicate: the result of each document shows; the text of the error is not the code.
  await docs.locator('input[name="doc"][value="eng-1"]').check();
  await docs.locator('input[name="doc"][value="eng-2"]').check();
  await docs.locator('button[type="submit"]').click();
  await expect(page.locator('.claire-toast').last()).toContainText('복사한 문서: 1개');
  await expect(docs.locator('#kb-curate-result')).toContainText('eng-2');
  await expect(docs.locator('#kb-curate-result')).not.toContainText('contribution_duplicate');
  expect(calls.filter((c) => c.method === 'POST' && c.path === '/kb/101/curations').map((c) => c.body)).toEqual([{ source: '104', documents: ['eng-1', 'eng-2'] }]);
  await expectNoRawKeys(page);

  // The source of the other server: the request has the record number of the source; the list has the documents of that knowledge base.
  await sources.nth(1).click();
  await expect(host.locator('[data-curate-src="remote:5"]')).toHaveClass(/active/);
  await expect.poll(() => calls.some((c) => c.method === 'GET' && c.path === '/kb/101/remote-sources?source=5')).toBe(true);
  const remoteDocs = host.locator('#kb-curate-docs');
  await expect(remoteDocs.locator('input[name="doc"]')).toHaveCount(2);
  await expect(remoteDocs).toContainText('Rail Map 1850');
  // A document that this knowledge base has is not selected again.
  const held = remoteDocs.locator('input[name="doc"][value="' + REMOTE_DOC_2 + '"]');
  await expect(held).toBeDisabled();
  await expect(held.locator('xpath=ancestor::label')).toContainText('이 지식 베이스에 있음');
  // The requests to copy from other servers: the state of each request as text, and the text of an error code.
  const requests = remoteDocs.locator('table tbody tr');
  await expect(requests).toHaveCount(2);
  await expect(requests.nth(0)).toContainText('Old Bridge');
  await expect(requests.nth(0)).toContainText('복사함');
  await expect(requests.nth(1)).toContainText('https://b.example/kb/901/documents/gone');
  await expect(requests.nth(1)).toContainText('실패');
  await expectNoRawKeys(page);

  // The copy request of a remote document: POST /kb/101/curations with "remote_source" (a number) and the IRI of the document.
  await remoteDocs.locator('input[name="doc"][value="' + REMOTE_DOC_1 + '"]').check();
  await remoteDocs.locator('button[type="submit"]').click();
  await expect(page.locator('.claire-toast').last()).toContainText('복사 요청: 1건');
  expect(calls.filter((c) => c.method === 'POST' && c.path === '/kb/101/curations')[1].body).toEqual({ remote_source: 5, documents: [REMOTE_DOC_1] });
  // The new request is in the table with the state "queued".
  await expect(remoteDocs.locator('table tbody tr')).toHaveCount(3);
  await expect(remoteDocs.locator('table tbody tr').nth(0)).toContainText('대기 중');
  // The curation tab stays the active tab after the reads.
  await expect(tab(page, 'curate')).toHaveAttribute('aria-selected', 'true');

  // "Read the list again" asks the server for the list again.
  const before = calls.filter((c) => c.path === '/kb/101/remote-sources?source=5').length;
  await remoteDocs.locator('#kb-curate-reload').click();
  await expect.poll(() => calls.filter((c) => c.path === '/kb/101/remote-sources?source=5').length).toBeGreaterThan(before);
  await expect(tab(page, 'curate')).toHaveAttribute('aria-selected', 'true');
  await expect(host.locator('[data-curate-src="remote:5"]')).toHaveClass(/active/);
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

test('/moderation/kb/101 curation: a list that the server reads, and a read that failed', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbmanage4');
  const state = makeState({ listing: { state: 'reading', error: null, read_at: null }, remoteReads: [] });
  await installApi(page, state);
  await page.goto('/moderation/kb/101', { waitUntil: 'domcontentloaded' });
  await tab(page, 'curate').click();
  const host = page.locator('#kb-curate-host');
  await host.locator('[data-curate-src="remote:5"]').click();
  const docs = host.locator('#kb-curate-docs');
  await expect(docs).toContainText('문서 목록을 읽고 있습니다');
  await expect(docs.locator('input[name="doc"]')).toHaveCount(0);
  // The read failed: the note has the reason, and the list can be read again.
  state.listing = { state: 'failed', error: 'remote_unreachable', read_at: null };
  await docs.locator('#kb-curate-reload').click();
  await expect(docs.locator('.claire-note.warn')).toContainText('다른 서버에서 읽지 못했습니다');
  await expect(docs.locator('#kb-curate-reload')).toBeVisible();
  await expectNoRawKeys(page);
  expect(errors).toEqual([]);
});

test('/moderation/kb/101 review queue: a copy whose origin was removed has two decisions; the moderation log shows a role name and the reason of the mutual collaboration as text', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbmanage2');
  const remote = { document: REMOTE_DOC_1, knowledge_base: 'https://b.example/kb/901/', credit: 'https://b.example/ap/users/alice' };
  const state = makeState({
    contributions: [
      contribution(9001, { title: 'Railway Timetable 1851', state: 'pending', approved: false, approved_at: null, review_help: { other_decisions: [{ number: '104', label: 'Steam Engines', decision: 'rejected', reason: 'off_subject' }], theme_score: 0.71, routed_by: 'server', kb_score: 0.42 }, origin: 'bulk' }),
      contribution(9010, { title: 'Rail Map 1850', state: 'hidden', origin: 'curation', contributor: null, contributor_reputation: undefined, remote, origin_removed: { at: 1700000900, reason: 'wrong_knowledge_type', waiting: true } }),
      contribution(9011, { title: 'Signal Rules', state: 'hidden', origin: 'curation', contributor: null, contributor_reputation: undefined, remote: { ...remote, document: REMOTE_DOC_2 }, origin_removed: { at: 1700000950, reason: 'spam', waiting: true } }),
      contribution(9003, { title: 'Canal Survey 1840' })
    ],
    log: [
      { number: '4', action: 'origin_removed', actor: null, target: '9010', reason: 'wrong_knowledge_type', created_at: 1700000900 },
      { number: '3', action: 'grant_collaborator', actor: { account: '1000001', display_name: 'E2E Admin' }, target: '5550001', reason: 'mutual_collaboration', created_at: 1700000300 },
      { number: '2', action: 'leave', actor: { account: '5550002', display_name: 'Carol' }, target: '5550002', reason: 'collaborator', created_at: 1700000200 },
      { number: '1', action: 'settings', actor: null, target: 'label', reason: '', created_at: 1700000000 }
    ]
  });
  const { calls, unknown } = await installApi(page, state);
  await page.goto('/moderation/kb/101', { waitUntil: 'domcontentloaded' });

  // The queue: the pending contribution, then the two copies that wait for a decision. The badge of the tab counts the three.
  const queue = page.locator('#kb-manage-tabs [role="tabpanel"][data-tabpanel="queue"]');
  const rows = queue.locator('[data-pick-pending]');
  await expect(rows).toHaveCount(3);
  await expect(tab(page, 'queue').locator('.claire-tab-badge')).toHaveText('3');
  await expect(rows.nth(0)).toContainText('Railway Timetable 1851');
  await expect(rows.nth(1)).toContainText('Rail Map 1850');
  await expect(rows.nth(1).locator('.claire-tag.warn')).toHaveText('원본에서 제거됨');
  await expect(rows.nth(1)).toContainText('선별');
  // A pending contribution has approve and reject, and the help for the decision as text.
  await expect(queue.locator('[data-q-approve="9001"]')).toBeVisible();
  await expect(queue.locator('[data-q-reject="9001"]')).toBeVisible();
  await expect(queue.locator('.claire-detail-col')).toContainText('0.71');
  await expect(queue.locator('.claire-detail-col')).toContainText('Steam Engines');
  await expect(queue.locator('.claire-detail-col')).toContainText('주제에 맞지 않음');
  await expectNoRawKeys(page);

  // The copy whose origin was removed: the note, the reason of the origin as text, the document of the origin, and the two decisions. It has no approve and no reject.
  await rows.nth(1).click();
  const detail = queue.locator('.claire-detail-col');
  await expect(detail.locator('h3')).toHaveText('Rail Map 1850');
  await expect(detail.locator('.claire-note.warn')).toContainText('원본 서버가 이 문서를 제거했습니다');
  await expect(detail.locator('dl')).toContainText('원본의 사유');
  await expect(detail.locator('dl')).toContainText('지식 유형이 다름');
  await expect(detail.locator('dl')).toContainText(REMOTE_DOC_1);
  await expect(detail.locator('[data-q-origin-keep="9010"]')).toHaveText('다시 표시');
  await expect(detail.locator('[data-q-origin-remove="9010"]')).toBeVisible();
  await expect(detail.locator('[data-q-approve], [data-q-reject]')).toHaveCount(0);
  await expect(detail.locator('a.claire-btn')).toHaveAttribute('href', '/kb/101/?doc=copy-9010');
  await expectNoRawKeys(page);

  // Decision 1, show the copy again: PATCH with the action "unhide". The row leaves the queue.
  await detail.locator('[data-q-origin-keep="9010"]').click();
  await expect(rows).toHaveCount(2);
  expect(calls.find((c) => c.method === 'PATCH' && c.path === '/kb/101/contributions/9010').body).toEqual({ action: 'unhide' });
  await expect(tab(page, 'queue').locator('.claire-tab-badge')).toHaveText('2');

  // Decision 2, remove the copy: the dialog asks for a reason code; POST …/remove has the reason.
  await rows.nth(1).click();
  await expect(detail.locator('h3')).toHaveText('Signal Rules');
  await detail.locator('[data-q-origin-remove="9011"]').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg).toBeVisible();
  await expect(dlg.locator('#rm-reason option')).toHaveText(['주제에 맞지 않음', '지식 유형이 다름', '중복', '품질이 낮음', '스팸', '기타']);
  await dlg.locator('#rm-reason').selectOption('spam');
  await expectNoRawKeys(page);
  await dlg.locator('.claire-dlg-foot .claire-btn.danger').click();
  await expect(dlg).toHaveCount(0);
  await expect(rows).toHaveCount(1);
  const removeCall = calls.find((c) => c.method === 'POST' && c.path === '/kb/101/contributions/9011/remove');
  expect(removeCall.body).toEqual({ reason: 'spam' });

  // The contributions tab: the copy that shows again, and the removed copy with the reason as text.
  await tab(page, 'content').click();
  const content = page.locator('#kb-manage-tabs [role="tabpanel"][data-tabpanel="content"]');
  await expect(content.locator('tbody tr')).toHaveCount(3);
  await expect(content.locator('tbody tr', { hasText: 'Signal Rules' })).toContainText('제거됨');
  await expect(content.locator('tbody tr', { hasText: 'Signal Rules' })).toContainText('스팸');
  // A copy of another server has no contributor account: the text for that case shows, not an empty cell.
  await expect(content.locator('tbody tr', { hasText: 'Rail Map 1850' }).locator('td').nth(1)).not.toHaveText('');
  // The menu of a visible contribution: hide, stop updates, and remove.
  await content.locator('tbody tr', { hasText: 'Canal Survey 1840' }).locator('.claire-menu-trigger').click();
  await expect(page.locator('[data-menu-action="hide"][data-menu-value="9003"]')).toBeVisible();
  await expect(page.locator('[data-menu-action="remove"][data-menu-value="9003"]')).toBeVisible();
  await page.locator('[data-menu-action="hide"][data-menu-value="9003"]').click();
  await expect.poll(() => calls.find((c) => c.method === 'PATCH' && c.path === '/kb/101/contributions/9003')?.body).toEqual({ action: 'hide' });
  await expect(content.locator('tbody tr', { hasText: 'Canal Survey 1840' })).toContainText('숨김');

  // The moderation log: the action as text; the reason "mutual_collaboration" and a role name as text, not as codes.
  await tab(page, 'log').click();
  const log = page.locator('#kb-log-host');
  const logRows = log.locator('tbody tr');
  await expect(logRows).toHaveCount(4);
  await expect(logRows.nth(0)).toContainText('원본에서 제거됨: 사본 숨김');
  await expect(logRows.nth(0)).toContainText('지식 유형이 다름');
  await expect(logRows.nth(1)).toContainText('협력자 역할 부여 (상호 협력)');
  await expect(logRows.nth(1).locator('td').nth(4)).toHaveText('상호 협력');
  await expect(logRows.nth(2)).toContainText('구성원 탈퇴');
  await expect(logRows.nth(2)).toContainText('Carol');
  await expect(logRows.nth(2).locator('td').nth(4)).toHaveText('협력자');
  await expect(logRows.nth(3)).toContainText('설정 변경');
  await expect(logRows.nth(3).locator('td').nth(4)).toHaveText('—');
  await expect(log).not.toContainText('mutual_collaboration');
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

test('/moderation/kb/101 archived: no approve, reject, hide, or remove control, no decision on a removed origin, and no curation tab', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbmanage3');
  const state = makeState({
    contributions: [
      contribution(9001, { title: 'Railway Timetable 1851', state: 'pending', approved: false, approved_at: null }),
      contribution(9010, { title: 'Rail Map 1850', state: 'hidden', origin: 'curation', contributor: null, contributor_reputation: undefined, remote: { document: REMOTE_DOC_1, knowledge_base: 'https://b.example/kb/901/', credit: 'https://b.example/ap/users/alice' }, origin_removed: { at: 1700000900, reason: 'spam', waiting: true } }),
      contribution(9003, { title: 'Canal Survey 1840' }),
      contribution(9004, { title: 'Hidden Ledger', state: 'hidden' }),
      contribution(9005, { title: 'Failed Merge', state: 'failed', reason: 'embedding_model_unavailable' })
    ]
  });
  state.kb.state = 'archived';
  const { calls, unknown } = await installApi(page, state);
  await page.goto('/moderation/kb/101', { waitUntil: 'domcontentloaded' });
  const host = page.locator('#settings-kb-manage-host');
  // The head tells that the knowledge base is archived and read-only.
  await expect(host.locator('.claire-card').first()).toContainText('보관됨');
  await expect(host.locator('.claire-card').first().locator('.claire-note.warn')).toBeVisible();
  // No curation tab.
  await expect(page.locator('#kb-manage-tabs [role="tab"]')).toHaveText([/심사 대기열/, '기여', '구성원과 역할', '설정', '모더레이션 기록']);
  await expect(tab(page, 'curate')).toHaveCount(0);
  await expect(page.locator('#kb-curate-host')).toHaveCount(0);

  // The queue shows the rows; no row has a decision control.
  const queue = page.locator('#kb-manage-tabs [role="tabpanel"][data-tabpanel="queue"]');
  const rows = queue.locator('[data-pick-pending]');
  await expect(rows).toHaveCount(2);
  await expect(queue.locator('.claire-detail-col h3')).toHaveText('Railway Timetable 1851');
  await expect(queue.locator('[data-q-approve], [data-q-reject]')).toHaveCount(0);
  await rows.nth(1).click();
  await expect(queue.locator('.claire-detail-col h3')).toHaveText('Rail Map 1850');
  await expect(queue.locator('[data-q-origin-keep], [data-q-origin-remove], [data-q-approve], [data-q-reject]')).toHaveCount(0);
  // The link to the document stays.
  await expect(queue.locator('.claire-detail-col a.claire-btn')).toBeVisible();

  // The contributions: each state shows, and no row has a menu (hide, show, stop updates, retry, remove).
  await tab(page, 'content').click();
  const content = page.locator('#kb-manage-tabs [role="tabpanel"][data-tabpanel="content"]');
  await expect(content.locator('tbody tr')).toHaveCount(4);
  await expect(content.locator('.claire-menu-trigger')).toHaveCount(0);
  await expect(content.locator('[data-menu-action]')).toHaveCount(0);
  await expectNoRawKeys(page);

  // The settings: the control puts the knowledge base back to active (PATCH with "state").
  await tab(page, 'settings').click();
  await expect(page.locator('#kb-archive-btn')).toHaveText('보관 해제');
  await page.locator('#kb-archive-btn').click();
  await expect.poll(() => calls.find((c) => c.method === 'PATCH' && c.path === '/kb/101/')?.body).toEqual({ state: 'active' });
  // The page is that of an active knowledge base again: the curation tab and the decision controls are back.
  await expect(tab(page, 'curate')).toHaveCount(1);
  await tab(page, 'queue').click();
  // The selected row is still the copy whose origin was removed.
  await expect(queue.locator('[data-q-origin-keep="9010"]')).toBeVisible();
  await expect(queue.locator('[data-q-origin-remove="9010"]')).toBeVisible();
  await rows.nth(0).click();
  await expect(queue.locator('[data-q-approve="9001"]')).toBeVisible();
  await expect(queue.locator('[data-q-reject="9001"]')).toBeVisible();
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

test('/moderation/kb/101 settings: a moderator makes the data bundle of the knowledge base and gets the download link, and corrects the knowledge type', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbmanage5');
  const state = makeState();
  const { calls, unknown } = await installApi(page, state);
  await page.goto('/moderation/kb/101', { waitUntil: 'domcontentloaded' });
  await tab(page, 'settings').click();
  const card = page.locator('#kb-bundle-card');
  await expect(card).toBeVisible();
  await expect(card.locator('h3')).toHaveText('데이터 번들');
  await expect(card.locator('.claire-hint').first()).toContainText('심사를 기다리는 기여와 숨긴 기여는 들어가지 않습니다');
  await expect(page.locator('#kb-bundle-download')).toHaveCount(0);
  await page.locator('#kb-bundle-btn').click();
  await expect.poll(() => calls.filter((c) => c.method === 'POST' && c.path === '/kb/101/graph/bundle').length).toBe(1);
  // The result: the counts of the bundle and the link with the token at the root of this server.
  await expect(card.locator('[data-kb-bundle-counts]')).toContainText('문서 30개');
  await expect(card.locator('[data-kb-bundle-counts]')).toContainText('기여 28개');
  await expect(page.locator('#kb-bundle-download')).toHaveAttribute('href', '/graph/bundle?token=gb3_e2eBundleToken');
  await expectNoRawKeys(page);

  // The knowledge type has its own dialog; the save of the form does not send it.
  const settingsForm = page.locator('#kb-manage-settings-form');
  await expect(settingsForm.locator('.claire-tag')).toHaveText('현실');
  await page.locator('#kb-type-btn').click();
  const typeDlg = page.locator('dialog.claire-dlg[open]');
  await typeDlg.locator('input[name="kb_type_change"][value="fictional"]').locator('..').click();
  await typeDlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect(typeDlg).toHaveCount(0);
  await expect.poll(() => calls.filter((c) => c.method === 'PATCH' && c.path === '/kb/101/').map((c) => c.body)).toEqual([{ knowledge_type: 'fictional' }]);
  await expect(page.locator('#kb-manage-settings-form .claire-tag')).toHaveText('허구');
  await page.locator('#kb-manage-settings-form button[type="submit"]').click();
  await expect.poll(() => calls.filter((c) => c.method === 'PATCH' && c.path === '/kb/101/').length).toBe(2);
  expect(Object.keys(calls.filter((c) => c.method === 'PATCH' && c.path === '/kb/101/')[1].body).sort()).toEqual(['description', 'join_policy', 'label', 'seed_entities']);
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});
