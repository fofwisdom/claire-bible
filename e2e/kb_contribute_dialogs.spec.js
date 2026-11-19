const { test, expect } = require('@playwright/test');
const { authenticateOwner, watchErrors, expectNoRawKeys, kbItem, contribution, installJsonApi } = require('./support/console_pages');

test.use({ serviceWorkers: 'block' });

// The document picker of the contribute dialogs (kb_dialogs.js "loadDocumentChoices") on /settings/kb. GET /documents gives one page: "documents", "next_before", "total"; the parameter "q" is the search text and "before" is the cursor of the next page. POST /submissions gives "_submission_view" (api/server.py).

// One document of the page in the shape of "queries.documents_list", with the two fields that "documents_route" adds for the own personal knowledge base.
function doc(id, title) {
  return { id, title, url: null, source_type: 'text', fetched_at: 1700000000, seen: 1, watch: 0, pinned: 0, hidden: 0, summary: '', has_presentation: false, has_detail: false, detail_format: 'adoc' };
}

const DOCS = [
  doc('d-1', 'Railway Timetable 1851'),
  doc('d-2', 'Canal Survey 1840'),
  doc('d-3', 'Railway Bridge Ledger'),
  doc('d-4', 'Steam Boiler Notes'),
  doc('d-5', 'Railway Signal Rules')
];

// The documents of the personal knowledge base of the account: "/documents" in single mode without a username, "/@<username>/documents" for an account with a username.
const DOCUMENTS_PATH = /^\/(@[^/]+\/)?documents$/;

async function installApi(page, opts) {
  const options = opts || {};
  const joined = kbItem(101, { label: 'Modern History', role: 'member', membership_state: 'active' });
  const personal = kbItem(10, { label: 'Personal Research', is_personal: true, is_public: false, account_id: 1, knowledge_type: null, url: null, active_moderators: undefined, stats: { documents: 5, entities: 44, relations: 30 } });
  delete personal.active_moderators;
  return installJsonApi(page, (u) => ['/kb', '/themes', '/submissions', '/documents', '/api/auto-rule', '/api/reputation', '/api/contributions', '/api/mutual-collaborations', '/api/contribution-blocks'].includes(u.pathname) || /^\/kb\/\d+\//.test(u.pathname) || DOCUMENTS_PATH.test(u.pathname), ({ path, method, query, body }) => {
    if (path === '/kb' && method === 'GET') return [{ type: 'OrderedCollection', totalItems: 1, knowledge_bases: [joined], personal_kb: personal, multi_theme: true }];
    if (path === '/themes') return [{ type: 'OrderedCollection', totalItems: 0, themes: [] }];
    if (path === '/submissions' && method === 'GET') return [{ submissions: [] }];
    if (path === '/api/auto-rule') return [{ server_switch: true, rule: null }];
    if (path === '/api/reputation') return [{ level: 'good', fiction_share: 0, fiction_like_documents: 0, visible_documents: 0, approval_rate: null, decisions: 0, limits: { pending: 5, auto_documents: null }, documents: [] }];
    if (path === '/api/contributions') return [{ contributions: [] }];
    if (path === '/api/mutual-collaborations') return [{ mutual_collaborations: [] }];
    if (path === '/api/contribution-blocks') return [{ documents: [] }];
    if (DOCUMENTS_PATH.test(path)) {
      const q = (query.get('q') || '').toLowerCase();
      const matched = DOCS.filter((d) => !q || d.title.toLowerCase().includes(q));
      // A page has two documents. The cursor is the identifier of the last document of the page.
      const before = query.get('before');
      const start = before ? matched.findIndex((d) => d.id === before) + 1 : 0;
      const pageDocs = matched.slice(start, start + 2);
      const last = start + 2 < matched.length ? pageDocs[pageDocs.length - 1].id : null;
      return [{ documents: pageDocs, format_status: null, kb_id: 10, theme_label: 'Personal Research', next_before: last, total: matched.length }];
    }
    if (path === '/submissions' && method === 'POST') {
      return [{ ok: true, submission: { number: '7001', created_at: 1700000000, items: body.documents.map((document) => (document === 'd-4'
        ? { document, title: 'Steam Boiler Notes', theme: null, theme_label: null, routed_by: null, score: null, targets: [], code: 'no_theme' }
        : { document, title: DOCS.find((d) => d.id === document).title, theme: '201', theme_label: 'Railway · Steam', routed_by: 'server', score: 0.71, targets: [{ knowledge_base: '101', label: 'Modern History', state: 'pending', contribution: '9002', approved: false }, { knowledge_base: '104', label: 'Steam Engines', state: 'skipped', reason: 'contribution_duplicate' }] })) } }, 201];
    }
    if (path === '/kb/101/contributions' && method === 'POST') {
      if (options.directFails && body.document === 'd-2') return [{ error: 'contribution_duplicate' }, 409];
      return [{ ok: true, contribution: contribution(9100, { document: 'copy-' + body.document, title: null, state: 'pending', approved: false, approved_at: null, contributor_reputation: undefined }) }, 201];
    }
    return null;
  });
}

test('the document picker: the search sends "q", "more" sends the cursor, the count line, and a checked document stays selected after a new search', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbdialog1');
  const { calls, unknown } = await installApi(page);
  await page.goto('/settings/kb', { waitUntil: 'domcontentloaded' });
  // The control is on the submissions tab.
  await page.locator('#settings-kb-tabs [role="tab"]').nth(1).click();
  await page.locator('#kb-submit-btn').click();
  const dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg).toBeVisible();
  const search = dlg.locator('#kb-doc-search');
  const boxes = dlg.locator('input[name="doc"]');
  const count = dlg.locator('[data-doc-count]');
  const more = dlg.locator('[data-doc-more]');
  const docCalls = () => calls.map((c) => c.path.replace(/^\/@[^/]+\//, '/')).filter((path) => path.startsWith('/documents'));

  // The first page: no parameter; two of five documents, and the control for the next page.
  await expect(dlg.locator('label[for="kb-doc-search"]')).toHaveText('문서 검색');
  await expect(boxes).toHaveCount(2);
  await expect(count).toHaveText('문서 5개 중 2개를 표시합니다.');
  expect(docCalls()).toEqual(['/documents']);
  await expect(more).toHaveText('문서 더 보기');
  await expectNoRawKeys(page);

  // More: the request has the cursor "next_before"; the rows of the next page go below the first rows.
  await more.click();
  await expect(boxes).toHaveCount(4);
  expect(docCalls()[1]).toBe('/documents?before=d-2');
  await expect(count).toHaveText('문서 5개 중 4개를 표시합니다.');
  await more.click();
  await expect(boxes).toHaveCount(5);
  expect(docCalls()[2]).toBe('/documents?before=d-4');
  await expect(count).toHaveText('문서 5개 중 5개를 표시합니다.');
  await expect(more).toHaveCount(0);

  // Select a document, then search: the request has "q", and the selected document that the search does not show stays in the list, checked.
  await dlg.locator('input[name="doc"][value="d-2"]').check();
  await search.fill('railway');
  await expect.poll(() => docCalls().includes('/documents?q=railway')).toBe(true);
  await expect(boxes).toHaveCount(3);
  await expect(dlg.locator('input[name="doc"][value="d-2"]')).toBeChecked();
  await expect(dlg.locator('input[name="doc"][value="d-2"]').locator('xpath=ancestor::label')).toContainText('Canal Survey 1840');
  await expect(dlg.locator('input[name="doc"][value="d-1"]')).not.toBeChecked();
  await expect(count).toHaveText('문서 3개 중 2개를 표시합니다.');
  // More with a search text: the request has "q" and the cursor.
  await more.click();
  await expect(boxes).toHaveCount(4);
  expect(docCalls()).toContain('/documents?q=railway&before=d-3');
  await expect(count).toHaveText('문서 3개 중 3개를 표시합니다.');
  await dlg.locator('input[name="doc"][value="d-5"]').check();

  // A search text without a match: the two selected documents stay, and the dialog does not close on the Enter key.
  await search.fill('zzz');
  await search.press('Enter');
  await expect.poll(() => docCalls().includes('/documents?q=zzz')).toBe(true);
  await expect(boxes).toHaveCount(2);
  await expect(dlg.locator('input[name="doc"]:checked')).toHaveCount(2);
  await expect(dlg).toBeVisible();
  expect(calls.some((c) => c.method === 'POST')).toBe(false);

  // The search field is empty again: the first page, with the selected documents checked.
  await search.fill('');
  await search.press('Enter');
  await expect(dlg.locator('input[name="doc"][value="d-1"]')).toBeVisible();
  await expect(dlg.locator('input[name="doc"][value="d-2"]')).toBeChecked();
  await expect(dlg.locator('input[name="doc"][value="d-5"]')).toBeChecked();
  await expect(dlg.locator('input[name="doc"][value="d-2"]')).toHaveCount(1);
  // A document that is not selected any more leaves the selection.
  await dlg.locator('input[name="doc"][value="d-5"]').uncheck();
  await expect(dlg.locator('input[name="doc"]:checked')).toHaveCount(1);
  await dlg.locator('input[name="doc"][value="d-1"]').check();
  await expectNoRawKeys(page);

  // Submit: POST /submissions has the selected documents and no theme. The dialog stays open with the result of each target.
  await dlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect.poll(() => calls.find((c) => c.method === 'POST' && c.path === '/submissions')?.body).toEqual({ documents: ['d-1', 'd-2'] });
  const result = dlg.locator('#claire-contrib-targets');
  await expect(result.locator('tbody tr')).toHaveCount(4);
  await expect(result).toContainText('Railway · Steam');
  await expect(result).toContainText('Modern History');
  await expect(result).toContainText('심사 대기');
  await expect(result).toContainText('건너뜀');
  await expect(result).not.toContainText('contribution_duplicate');
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});

test('the direct contribution dialog: the same document picker, one request for each selected document, and the text of a refusal', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  const errors = watchErrors(page);
  await authenticateOwner(page, 'kbdialog2');
  const { calls, unknown } = await installApi(page, { directFails: true });
  await page.goto('/settings/kb', { waitUntil: 'domcontentloaded' });
  // The control is on the submissions tab.
  await page.locator('#settings-kb-tabs [role="tab"]').nth(1).click();
  await page.locator('#kb-submit-btn').click();
  let dlg = page.locator('dialog.claire-dlg[open]');
  // Without a selected document the dialog asks for one and sends nothing.
  await dlg.locator('.claire-dlg-foot .claire-btn.pri').click();
  await expect(dlg.locator('#claire-contrib-targets')).toContainText('문서를 하나 이상 고르십시오.');
  expect(calls.some((c) => c.method === 'POST')).toBe(false);
  // "Contribute directly to a shared knowledge base" opens the second dialog.
  await dlg.locator('.claire-dlg-foot .claire-btn.sec', { hasText: '공유 지식 베이스에 직접 기여' }).click();
  dlg = page.locator('dialog.claire-dlg[open]');
  await expect(dlg.locator('h2')).toContainText('공유 지식 베이스에 직접 기여');
  await expect(dlg.locator('#kb-doc-search')).toBeVisible();
  await expect(dlg.locator('input[name="doc"]')).toHaveCount(2);
  await expect(dlg.locator('[data-doc-count]')).toHaveText('문서 5개 중 2개를 표시합니다.');
  // The shared knowledge bases where the account is a member, with the role as text.
  const kbRow = dlg.locator('#claire-no-theme-list tbody tr');
  await expect(kbRow).toHaveCount(1);
  await expect(kbRow).toContainText('Modern History');
  await expect(kbRow).toContainText('구성원');
  expect(calls.some((c) => c.path === '/kb?joined=true')).toBe(true);
  await expectNoRawKeys(page);

  // No selected document: the note, and no request.
  await dlg.locator('[data-pick-kb="101"]').click();
  await expect(dlg.locator('#claire-direct-msg')).toHaveText('문서를 하나 이상 고르십시오.');
  // Two documents, one in the search result: one POST for each document. The refusal of one document shows as text, and the dialog stays open.
  await dlg.locator('input[name="doc"][value="d-2"]').check();
  await dlg.locator('#kb-doc-search').fill('signal');
  await expect(dlg.locator('input[name="doc"][value="d-5"]')).toBeVisible();
  await dlg.locator('input[name="doc"][value="d-5"]').check();
  await dlg.locator('[data-pick-kb="101"]').click();
  await expect.poll(() => calls.filter((c) => c.method === 'POST' && c.path === '/kb/101/contributions').map((c) => c.body)).toEqual([{ document: 'd-2' }, { document: 'd-5' }]);
  await expect(dlg.locator('#claire-direct-msg')).toBeVisible();
  await expect(dlg.locator('#claire-direct-msg')).not.toContainText('contribution_duplicate');
  await expect(dlg.locator('#claire-direct-msg')).not.toHaveText('');
  await expect(page.locator('.claire-toast').last()).toContainText('기여를 보냈습니다.');
  await expect(dlg).toBeVisible();
  await expectNoRawKeys(page);
  expect(unknown).toEqual([]);
  expect(errors).toEqual([]);
});
