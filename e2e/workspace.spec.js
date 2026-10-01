const { test, expect } = require('@playwright/test');

async function waitForClaire(page) {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.authScope),
  ).toBe('anonymous');
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.scale),
  ).not.toBeNull();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.stabilized),
  ).toBe(true);
}

async function expectNoHorizontalOverflow(page) {
  const size = await page.evaluate(() => ({
    client: document.documentElement.clientWidth,
    scroll: document.documentElement.scrollWidth,
  }));
  expect(size.scroll).toBeLessThanOrEqual(size.client);
}

test('mobile primary view defaults to doc list, bottom bar is removed, and more menu is positioned to the right of theme button', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. 기본 보기가 본문 목록(docs)으로 표시되는지 확인
  await expect(page.locator('#docs')).toBeVisible();
  await expect(page.locator('#netwrap')).toBeHidden();

  // 2. 바텀 바 및 단추 제거 확인
  await expect(page.locator('#worktabs')).toBeHidden();

  // 3. 라이트/다크모드 전환 단추 오른쪽에 상세 메뉴 꺼내기(#morebtn) 단추가 위치하는지 확인
  const themebtn = page.locator('#themebtn');
  const morebtn = page.locator('#morebtn');
  await expect(themebtn).toBeVisible();
  await expect(morebtn).toBeVisible();

  const themeBox = await themebtn.boundingBox();
  const moreBox = await morebtn.boundingBox();
  expect(themeBox).not.toBeNull();
  expect(moreBox).not.toBeNull();
  expect(moreBox.x).toBeGreaterThan(themeBox.x);
  expect(moreBox.height).toBeGreaterThanOrEqual(38);
  expect(moreBox.width).toBeGreaterThanOrEqual(38);

  // 4. 상세 메뉴 꺼내기 클릭 시 detailpane 열림 및 닫힘
  await morebtn.click();
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();
  await page.locator('#detailclose').click();
  await expect(detailPane).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();

  // 5. 본문 목록에서 문서 선택 시 본문 읽기(reader) 모달 호출 및 닫기
  const docItems = page.locator('#doclist .docitem');
  await expect(docItems.first()).toBeVisible();
  await docItems.first().click();

  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();
  await expect(reader).toHaveAttribute('aria-modal', 'true');
  expect(await page.locator('body').evaluate(body => body.classList.contains('reader-open'))).toBe(true);

  // 6. 리더 닫기 버튼 클릭 시 다시 본문 목록으로 복귀
  await page.locator('#reader .rclose').click();
  await expect(reader).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('tablet and desktop layouts do not squeeze the graph into three fixed columns', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);
  await expect(page.locator('#worktabs')).toBeHidden();
  await expect(page.locator('#morebtn')).toBeVisible();
  await expect(page.locator('#graphdocnav')).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect.poll(async () => {
    const box = await page.locator('#netwrap').boundingBox();
    return box ? box.width : 0;
  }).toBeGreaterThan(600);
  await expect(page.locator('#detailpane')).toBeHidden();

  await page.locator('#morebtn').click();
  await expect(page.locator('#moremenu')).toBeVisible();
  await expect(page.locator('#authstate')).toContainText('익명 읽기전용');
  await expect(page.locator('#synthbtn')).toBeHidden();
  await expect(page.locator('#addbtn')).toBeHidden();
  await expect(page.locator('#dedupbtn')).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.locator('#moremenu')).toBeHidden();

  await page.setViewportSize({ width: 1600, height: 900 });
  await expectNoHorizontalOverflow(page);
  await expect(page.locator('#morebtn')).toBeHidden();
  await expect(page.locator('#worktabs')).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect(page.locator('#detailpane')).toBeVisible();
});


test('right menu compact icon mode toggles and reduces width on desktop', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Right detailpane is visible and aria-hidden is false on desktop
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();
  await expect(detailPane).toHaveAttribute('aria-hidden', 'false');

  // 2. Initial expanded state: detailpane width is >= 300px
  const initialBox = await detailPane.boundingBox();
  expect(initialBox.width).toBeGreaterThanOrEqual(300);

  // 3. Toggle button exists in detailhead
  const toggleBtn = page.locator('#detailtogglebtn');
  await expect(toggleBtn).toBeVisible();

  // 4. Click toggle button to switch to compact icon rail mode
  await toggleBtn.click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.detailCompact),
  ).toBe(true);

  // 5. In compact mode, width is reduced (<= 65px) and buttons remain accessible
  await expect.poll(async () => {
    const box = await detailPane.boundingBox();
    return box ? box.width : 999;
  }).toBeLessThanOrEqual(65);

  const actionBtn = page.locator('#themebtn');
  await expect(actionBtn).toBeVisible();
  await expect(actionBtn).toHaveAttribute('aria-label');

  // 6. Click toggle button again to restore full width
  await toggleBtn.click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.detailCompact),
  ).toBe(false);

  await expect.poll(async () => {
    const box = await detailPane.boundingBox();
    return box ? box.width : 0;
  }).toBeGreaterThanOrEqual(300);

  expect(pageErrors).toEqual([]);
});

test('inspecting node on desktop displays details without backdrop dimming or click blocking', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Switch right menu to compact mode first
  const isCompact = await page.evaluate(() => window.claireDebug.detailCompact);
  if (!isCompact) {
    const toggleBtn = page.locator('#detailtogglebtn');
    await toggleBtn.click();
  }
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.detailCompact),
  ).toBe(true);

  // 2. Select document and switch to graph
  await page.locator('.docitem').first().evaluate(element => element.click());
  await page.evaluate(() => revealWorkspace('graph'));
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activePane),
  ).toBe('graph');

  // 3. Inspect a visible node
  await page.waitForTimeout(600);
  const point = await page.evaluate(() => {
    const box = document.getElementById('net').getBoundingClientRect();
    return window.claireDebug.visibleNodePoints().find(
      item => item.x > 40 && item.y > 40 && item.x < box.width - 40 && item.y < box.height - 40,
    ) || window.claireDebug.visibleNodePoints()[0] || null;
  });
  expect(point).not.toBeNull();
  await page.locator('#net').click({ position: { x: Math.round(point.x), y: Math.round(point.y) }, force: true });

  // 4. Detailpane automatically expands and displays panel content
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.detailCompact),
  ).toBe(false);

  const detailPane = page.locator('#detailpane');
  await expect.poll(async () => {
    const box = await detailPane.boundingBox();
    return box ? box.width : 0;
  }).toBeGreaterThanOrEqual(300);

  // 5. Drawer backdrop must NOT be visible on desktop
  const backdrop = page.locator('#drawerbackdrop');
  await expect(backdrop).toBeHidden();

  // 6. Panel has content and is visible
  const panel = page.locator('#panel');
  await expect(panel).toBeVisible();
  await expect(panel).not.toBeEmpty();

  expect(pageErrors).toEqual([]);
});

test('mobile history back navigation closes modal and returns to previous view without exiting', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. 기본 보기가 본문 목록(docs)이고 modal은 닫혀있음
  await expect(page.locator('#docs')).toBeVisible();
  const reader = page.locator('#reader');
  await expect(reader).toBeHidden();

  // 2. 본문 목록에서 문서 클릭하여 리더 열기
  await page.locator('.docitem').first().click();
  await expect(reader).toBeVisible();
  await expect(reader).toHaveAttribute('aria-modal', 'true');
  expect(await page.locator('body').evaluate(body => body.classList.contains('reader-open'))).toBe(true);

  // 3. 브라우저 뒤로가기 실행 -> 리더 닫히고 본문 목록 유지
  await page.goBack();
  await expect(reader).toBeHidden();
  expect(await page.locator('body').evaluate(body => body.classList.contains('reader-open'))).toBe(false);
  await expect(page.locator('#docs')).toBeVisible();

  // 4. 상단 우측의 상세 메뉴(#morebtn) 열기
  await page.locator('#morebtn').click();
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();

  // 5. 브라우저 뒤로가기 실행 -> 드로어 닫히고 본문 목록 유지
  await page.goBack();
  await expect(detailPane).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();

  // 6. 리더 열고 ✕ 닫기 단추로 닫기
  await page.locator('.docitem').first().click();
  await expect(reader).toBeVisible();
  await page.locator('#reader .rclose').click();
  await expect(reader).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('mobile reader can be opened and closed, and drawer menu opens above document list', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. 본문 목록에서 문서 클릭하여 리더 모달 열기
  await page.locator('.docitem').first().click();
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();
  await expect(reader).toHaveAttribute('aria-modal', 'true');

  // 2. 리더 닫기 버튼(✕) 클릭하여 리더 닫기
  await page.locator('#reader .rclose').click();
  await expect(reader).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();

  // 3. 상단의 햄버거 메뉴(#morebtn) 클릭
  const moreBtn = page.locator('#morebtn');
  await expect(moreBtn).toBeVisible();
  await moreBtn.click();

  // 4. Detailpane과 backdrop이 표시되는지 확인
  const detailPane = page.locator('#detailpane');
  const backdrop = page.locator('#drawerbackdrop');
  await expect(detailPane).toBeVisible();
  await expect(backdrop).toBeVisible();

  // 5. 닫기 단추(✕)로 드로어 닫기
  await page.locator('#detailclose').click();
  await expect(detailPane).toBeHidden();
  await expect(backdrop).toBeHidden();

  // 6. 본문 목록이 계속 활성화되어 있는지 확인
  await expect(page.locator('#docs')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('mobile reader extends to bottom and displays text to the end without obstruction', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. 리더 모달 열기
  await page.locator('.docitem').first().click();
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();

  // 2. 바텀 바가 없으므로 리더 박스가 화면 바닥까지 도달하고 가로 스크롤 없음
  const readerBox = await reader.boundingBox();
  expect(readerBox).not.toBeNull();
  expect(readerBox.y + readerBox.height).toBeGreaterThanOrEqual(840);

  const rbodyMetrics = await page.evaluate(() => {
    const b = document.getElementById('rbody');
    return {
      clientWidth: b.clientWidth,
      scrollWidth: b.scrollWidth,
      scrollbarWidth: window.getComputedStyle(b).scrollbarWidth,
    };
  });
  expect(rbodyMetrics.scrollWidth).toBeLessThanOrEqual(rbodyMetrics.clientWidth);
  expect(rbodyMetrics.scrollbarWidth).toBe('none');

  // 3. rbody 스크롤 끝까지 내렸을 때 마지막 자식 요소가 화면 내에 표시됨
  await page.evaluate(() => {
    const b = document.getElementById('rbody');
    if (b) b.scrollTop = b.scrollHeight;
  });
  await page.waitForTimeout(200);

  const lastChildState = await page.evaluate(() => {
    const b = document.getElementById('rbody');
    const lastChild = b.lastElementChild || b;
    const rect = lastChild.getBoundingClientRect();
    return {
      lastChildBottom: rect.bottom,
      viewportHeight: window.innerHeight,
      isCompletelyVisible: rect.bottom <= window.innerHeight,
    };
  });

  expect(lastChildState.isCompletelyVisible).toBe(true);

  expect(pageErrors).toEqual([]);
});

test('shared document page allows scrolling and maintains visible fixed rail track when scrolling', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));

  await page.setViewportSize({ width: 1024, height: 768 });
  await page.goto('/p?s=23456789abcdefgh', { waitUntil: 'domcontentloaded' });

  // 1. Verify title and heading rendered
  await expect(page.locator('h1')).toContainText('테스트 문서 1');

  // 2. Verify page scrollability styles and hidden scrollbars
  const scrollStyles = await page.evaluate(() => {
    const htmlStyle = window.getComputedStyle(document.documentElement);
    const bodyStyle = window.getComputedStyle(document.body);
    return {
      htmlOverflowY: htmlStyle.overflowY,
      bodyOverflowY: bodyStyle.overflowY,
      htmlScrollbarWidth: htmlStyle.scrollbarWidth,
      bodyScrollbarWidth: bodyStyle.scrollbarWidth,
      bodyHeight: bodyStyle.height,
      scrollHeight: document.documentElement.scrollHeight,
      innerHeight: window.innerHeight,
    };
  });

  expect(scrollStyles.bodyOverflowY).not.toBe('hidden');
  expect(scrollStyles.scrollHeight).toBeGreaterThan(scrollStyles.innerHeight);
  expect(scrollStyles.htmlScrollbarWidth).toBe('none');
  expect(scrollStyles.bodyScrollbarWidth).toBe('none');

  // 3. Verify #srail has position: fixed and is visible
  const rail = page.locator('#srail');
  await expect(rail).toBeVisible();

  const railPosition = await rail.evaluate(el => window.getComputedStyle(el).position);
  expect(railPosition).toBe('fixed');

  const initialRailBox = await rail.boundingBox();
  expect(initialRailBox).not.toBeNull();
  expect(initialRailBox.y).toBeGreaterThanOrEqual(10);
  expect(initialRailBox.y + initialRailBox.height).toBeLessThanOrEqual(768);

  // 4. Verify diamond markers rendered
  const markers = page.locator('.cb-diamond-marker');
  const markerCount = await markers.count();
  expect(markerCount).toBeGreaterThanOrEqual(5);

  // 5. Scroll page down using window.scrollTo and verify rail stays in exact same viewport position
  await page.evaluate(() => window.scrollTo({ top: 600, behavior: 'instant' }));
  await page.waitForTimeout(200);

  const scrolledY = await page.evaluate(() => window.scrollY);
  expect(scrolledY).toBeGreaterThanOrEqual(500);

  // The rail MUST still be visible and at the exact same screen coordinates!
  await expect(rail).toBeVisible();
  const scrolledRailBox = await rail.boundingBox();
  expect(scrolledRailBox).not.toBeNull();
  expect(Math.abs(scrolledRailBox.y - initialRailBox.y)).toBeLessThanOrEqual(2);
  expect(Math.abs(scrolledRailBox.height - initialRailBox.height)).toBeLessThanOrEqual(2);

  // 6. Click a diamond marker to navigate and verify smooth navigation and rail persistence
  const targetMarker = markers.nth(3);
  await targetMarker.click();
  await page.waitForTimeout(400);

  // Rail MUST still be visible after marker navigation
  await expect(rail).toBeVisible();
  const afterNavRailBox = await rail.boundingBox();
  expect(afterNavRailBox).not.toBeNull();
  expect(Math.abs(afterNavRailBox.y - initialRailBox.y)).toBeLessThanOrEqual(2);

  // 7. Verify wheel scroll works without impediment
  await page.mouse.wheel(0, -300);
  await page.waitForTimeout(200);
  await expect(rail).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('anonymous users can generate and open share link in public mode', async ({ page, context }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Verify anonymous readonly scope
  const whoami = await page.evaluate(async () => {
    const res = await fetch('/whoami');
    return res.json();
  });
  expect(whoami.scope).toBe('anonymous');

  // 2. Select document from left panel to open reader
  const firstDocItem = page.locator('#doclist .docitem').first();
  await expect(firstDocItem).toBeVisible();
  await firstDocItem.click();

  // 3. Verify original reader share button (#reader .head .rshare) is visible
  const readerShareBtn = page.locator('#reader .head .rshare');
  await expect(readerShareBtn).toBeVisible();
  await expect(readerShareBtn).toHaveAttribute('title', '공유 링크 만들기');

  // 4. Click original share button in reader header and verify share link generation
  const [shareResponse] = await Promise.all([
    page.waitForResponse(res => res.url().includes('/share') && res.request().method() === 'POST'),
    readerShareBtn.click(),
  ]);
  expect(shareResponse.status()).toBe(200);
  const shareData = await shareResponse.json();
  expect(shareData.path).toMatch(/^\/p\?s=/);

  // 5. Verify reader sharebox displays the generated URL
  const shareBox = page.locator('#reader #sharebox');
  await expect(shareBox).toBeVisible();
  const shareInput = shareBox.locator('#shareurl');
  await expect(shareInput).toHaveValue(new RegExp(shareData.path.replace('?', '\\?')));

  // 6. Open the generated share link in a new page and verify content loads
  const sharedPage = await context.newPage();
  const sharedResponse = await sharedPage.goto(shareData.path);
  expect(sharedResponse.status()).toBe(200);
  await expect(sharedPage.locator('h1')).toBeVisible();
  const titleText = await sharedPage.locator('h1').textContent();
  expect(titleText.length).toBeGreaterThan(0);
  await expect(sharedPage.locator('.doc-content').first()).toBeVisible();
  const bodyText = await sharedPage.locator('.doc-content').first().textContent();
  expect(bodyText.length).toBeGreaterThan(10);
  await sharedPage.close();

  expect(pageErrors).toEqual([]);
});

test('pdf parser fallback, encoding flaw, and scanned tags render in reader and public share', async ({ page, context }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Find and click doc-3 in the document list
  const doc3Item = page.locator('#doclist .docitem').filter({ hasText: 'PDF 결함/폴백 테스트' });
  await expect(doc3Item).toBeVisible();
  await doc3Item.click();

  // 2. Verify reader opens and contains parser fallback, encoding flaw, and scanned tags
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();

  const fallbackTag = reader.locator('.parser-fallback-tag');
  await expect(fallbackTag).toBeVisible();
  await expect(fallbackTag).toContainText('PyPDFium2 폴백 (PyPDF)');
  await expect(fallbackTag).toHaveAttribute('title', /Encoding flaws detected: unmapped_cid_fonts/);

  const flawTag = reader.locator('.encoding-flaw-tag');
  await expect(flawTag).toBeVisible();
  await expect(flawTag).toContainText('⚠️ PDF 인코딩 결함');
  await expect(flawTag).toHaveAttribute('title', /unmapped_cid_fonts/);

  const scannedTag = reader.locator('.scanned-tag');
  await expect(scannedTag).toBeVisible();
  await expect(scannedTag).toContainText('📷 스캔본 PDF');

  // 3. Verify public share page (/p?s=34567892abcdefgh) also renders these tags
  const sharedPage = await context.newPage();
  const sharedResponse = await sharedPage.goto('/p?s=34567892abcdefgh');
  expect(sharedResponse.status()).toBe(200);

  const shareFallbackTag = sharedPage.locator('.parser-fallback-tag');
  await expect(shareFallbackTag).toBeVisible();
  await expect(shareFallbackTag).toContainText('PyPDFium2 폴백 (PyPDF)');

  const shareFlawTag = sharedPage.locator('.encoding-flaw-tag');
  await expect(shareFlawTag).toBeVisible();
  await expect(shareFlawTag).toContainText('⚠️ PDF 인코딩 결함');

  const shareScannedTag = sharedPage.locator('.scanned-tag');
  await expect(shareScannedTag).toBeVisible();
  await expect(shareScannedTag).toContainText('📷 스캔본 PDF');

  await sharedPage.close();
  expect(pageErrors).toEqual([]);
});

test('node aliases render and can be promoted to primary representative label in owner session', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });

  // 1. Authenticate as owner
  await page.goto('/?t=e2e-owner-token-0123456789abcdef', { waitUntil: 'domcontentloaded' });
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.authScope),
  ).toBe('owner');
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.stabilized),
  ).toBe(true);

  // 2. Select document 1 and switch to graph
  await page.locator('.docitem').first().evaluate(element => element.click());
  await page.evaluate(() => revealWorkspace('graph'));
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.activePane),
  ).toBe('graph');

  // 3. Inspect ent-1
  await page.evaluate(() => loadNode('ent-1'));

  // 4. Verify panel renders ent-1 with aliases section
  const panel = page.locator('#panel');
  await expect(panel.locator('h2')).toContainText('엔티티 A');
  const aliasesSection = panel.locator('.node-aliases-section');
  await expect(aliasesSection).toBeVisible();
  await expect(aliasesSection.locator('.al-label')).toContainText('별칭 (2)');

  const chips = aliasesSection.locator('.node-alias-chip');
  await expect(chips).toHaveCount(2);
  await expect(chips.first()).toContainText('별칭 A1');

  // 5. Check '노드 관리' button exists next to '종합에 추가' and open dropdown
  const manageBtn = panel.locator('#node-manage-btn');
  await expect(manageBtn).toBeVisible();
  await expect(manageBtn).toContainText('노드 관리');
  await manageBtn.click();

  // 6. Click '대표 별칭' from dropdown menu to enter selection mode
  const aliasMenuBtn = panel.locator('#node-manage-alias-btn');
  await expect(aliasMenuBtn).toBeVisible();
  await expect(aliasMenuBtn).toContainText('대표 별칭');
  await aliasMenuBtn.click();

  // 7. Verify chips become selectable, and click the first selectable chip
  const selectableChips = aliasesSection.locator('button.node-alias-chip.selectable');
  await expect(selectableChips).toHaveCount(2);
  const targetChip = selectableChips.first();
  await expect(targetChip).toContainText('별칭 A1');

  // 8. Handle confirm dialog and click promote
  page.once('dialog', dialog => dialog.accept());
  await targetChip.click();

  // 9. Verify representative label changed to '별칭 A1'
  await expect(panel.locator('h2')).toContainText('별칭 A1');

  // 8. Verify old name '엔티티 A' is now in alias chips, and '별칭 A1' is not
  await expect(aliasesSection.locator('.node-alias-chip')).toHaveCount(2);
  await expect(aliasesSection).toContainText('엔티티 A');
  await expect(aliasesSection).not.toContainText('별칭 A1');

  // 9. Verify graph dataset label updated for ent-1
  const graphLabel = await page.evaluate(() => {
    const node = allNodes.get('ent-1');
    return node ? node.label : null;
  });
  expect(graphLabel).toBe('별칭 A1');

  expect(pageErrors).toEqual([]);
});

test('provider management modal opens in owner session, displays providers, and supports connection test', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });

  // 1. Verify anonymous user does NOT see provider button
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.authScope),
  ).toBe('anonymous');
  await expect(page.locator('#providermanagebtn')).toBeHidden();

  // 2. Authenticate as owner
  await page.goto('/?t=e2e-provider-token-0123456789abcdef', { waitUntil: 'domcontentloaded' });
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.authScope),
  ).toBe('owner');
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.stabilized),
  ).toBe(true);

  // 3. Provider button should now be visible in owner session
  const providerBtn = page.locator('#providermanagebtn');
  await expect(providerBtn).toBeVisible();

  // 4. Open provider manager modal
  await providerBtn.click();
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();

  // 5. Verify provider manager UI components render
  await expect(detailPane.locator('h2')).toContainText('⚡ LLM & 프로바이더 관리');
  const activeSelect = page.locator('#prov-active-select');
  await expect(activeSelect).toBeVisible();
  await expect(page.locator('#prov-gemini-model')).toBeVisible();
  await expect(page.locator('#prov-agy-bin')).toBeVisible();
  await expect(page.locator('#prov-cdx-bin')).toBeVisible();
  await expect(page.locator('#prov-oai-url')).toBeVisible();

  // 6. Test connection button for antigravity
  const agyTestBtn = detailPane.locator('button:has-text("CLI 확인")').first();
  await expect(agyTestBtn).toBeVisible();
  await agyTestBtn.click();
  const agyResult = page.locator('#prov-test-res-antigravity');
  await expect(agyResult).toBeVisible();
  await expect(agyResult).toContainText(/감지됨|바이너리|호스트|성공/);

  // 7. Save settings and verify notification alert
  const saveBtn = detailPane.locator('button:has-text("💾 설정 저장")');
  await expect(saveBtn).toBeVisible();
  await saveBtn.click();
  await expect(page.getByText('프로바이더 설정이 성공적으로 저장되었습니다')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('decision stream and heatmap matrix are separate features with proper audit stream and matrix confirm', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);

  // 1. Verify center tab button for Decision Stream is present in common header
  const streamTabBtn = page.locator('#centertab-stream');
  await expect(streamTabBtn).toBeVisible();
  await expect(streamTabBtn).toContainText('의사결정 스트림');

  // 2. Click the Decision Stream tab button to open global audit stream in center view
  await streamTabBtn.click();

  // 3. Verify streamwrap opens as center view with Decision Stream header, filter bar, and cards
  const streamWrap = page.locator('#streamwrap');
  await expect(streamWrap).toBeVisible();
  await expect(streamWrap).toHaveAttribute('aria-label', '의사결정 스트림');
  await expect(streamWrap.locator('#stream-filter-bar')).toBeVisible();

  // 4. Open doc-2 in reader by clicking its item in document list
  const doc2Item = page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 2' });
  await expect(doc2Item).toBeVisible();

  // 5. Simulate heatmap matrix in sessionStorage
  await page.evaluate(() => {
    sessionStorage.setItem('doc_matrix_doc-2', JSON.stringify({
      document_id: 'doc-2',
      rows: ['엔티티 B', '엔티티 C'],
      cols: ['엔티티 A', '엔티티 B'],
      matrix: [[1.0, 0.2], [0.3, 0.42]],
      threshold_auto_merge: 0.93,
      threshold_borderline: 0.72
    }));
  });

  // Re-open reader for doc-2 to trigger matrix banner rendering
  await doc2Item.click();
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();

  // 6. Verify matrix preview banner is visible in reader until confirmed
  const matrixBanner = page.locator('#doc-matrix-banner-doc-2');
  await expect(matrixBanner).toBeVisible();
  await expect(matrixBanner).toContainText('지식 대조 히트맵 매트릭스');

  // 7. Click '전체화면 매트릭스' button
  const fullMatrixBtn = matrixBanner.locator('button:has-text("전체화면 매트릭스")');
  await fullMatrixBtn.click();

  // 8. Verify #matrixwrap is displayed as center view with pure visual grid
  const matrixWrap = page.locator('#matrixwrap');
  await expect(matrixWrap).toBeVisible();
  await expect(matrixWrap.locator('#matrix-title')).toContainText('지식 대조 히트맵 매트릭스');
  await expect(page.locator('#matrix-view-grid')).toBeVisible();

  // 9. Confirm and close matrix view
  const confirmMatrixBtn = page.locator('#matrix-confirm-btn');
  await confirmMatrixBtn.click();
  await expect(matrixWrap).toBeHidden();

  // 10. Re-open reader and verify banner is removed and confirmation is recorded permanently
  await doc2Item.click();
  await expect(page.locator('#doc-matrix-banner-doc-2')).toBeHidden();
  const isConfirmed = await page.evaluate(() => localStorage.getItem('doc_matrix_confirmed_doc-2') === '1');
  expect(isConfirmed).toBe(true);

  expect(pageErrors).toEqual([]);
});

test('heatmap matrix replaces graph during ingestion with clean progress and live similarity matrix', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Initial state: graph canvas is visible, matrixwrap is hidden
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect(page.locator('#matrixwrap')).toBeHidden();

  // 2. Simulate running ingest by invoking initIngestMatrixView + setCenterView('matrix')
  await page.evaluate(() => {
    window.initIngestMatrixView('https://example.com/test-article', '시스템 아키텍처', '기본 지식베이스');
    window.setCenterView('matrix');
  });

  // 3. Verify graph is hidden and matrixwrap is prominently visible without artificial placeholder clutter
  await expect(page.locator('#netwrap')).toBeHidden();
  const matrixWrap = page.locator('#matrixwrap');
  await expect(matrixWrap).toBeVisible();
  await expect(matrixWrap.locator('#matrix-title')).toContainText('지식 대조 히트맵 매트릭스');
  await expect(matrixWrap.locator('#matrix-target-doc')).toContainText('대조 대상: https://example.com/test-article');
  await expect(page.locator('#matrix-progress-banner')).toBeVisible();
  await expect(matrixWrap.locator('#matrix-progress-msg')).toHaveText('원문 분석 및 엔티티 대조 준비 중…');

  // 4. Simulate streaming heatmap_matrix event arriving during active ingest
  await page.evaluate(() => {
    window.renderHeatmapMatrix({
      rows: ['엔티티 1', '엔티티 2'],
      cols: ['후보 A', '후보 B'],
      matrix: [[0.95, 0.3], [0.81, 0.4]],
      threshold_auto_merge: 0.93,
      threshold_borderline: 0.72
    }, '테스트 문서', true);
    window.updateMatrixProgress('후보 엔티티 대조 완료');
  });

  await expect(matrixWrap.locator('#matrix-progress-msg')).toHaveText('후보 엔티티 대조 완료');
  await expect(page.locator('#matrix-tbody tr')).toHaveCount(2);

  // Verify pure color cells (no text/numbers inside cell)
  const cellTexts = await page.locator('#matrix-tbody .matrix-cell').allTextContents();
  expect(cellTexts.every(t => t.trim() === '')).toBe(true);

  // Verify multi-tier color assignment and ingest animation class
  const mergeCell = page.locator('#matrix-tbody .matrix-cell[data-tier="merge"]');
  await expect(mergeCell).toBeVisible();
  await expect(mergeCell).toHaveClass(/cell-ingest-anim/);

  const borderlineCell = page.locator('#matrix-tbody .matrix-cell[data-tier="borderline"]');
  await expect(borderlineCell).toBeVisible();

  const midCell = page.locator('#matrix-tbody .matrix-cell[data-tier="mid"]');
  await expect(midCell).toBeVisible();

  // Verify static view (outside ingest) has no ingest animation class and hides spinner with completed message
  await page.evaluate(() => {
    window.renderHeatmapMatrix({
      rows: ['엔티티 1', '엔티티 2'],
      cols: ['후보 A', '후보 B'],
      matrix: [[0.95, 0.3], [0.81, 0.4]],
      threshold_auto_merge: 0.93,
      threshold_borderline: 0.72
    }, '완료 문서', false);
  });
  await expect(page.locator('#matrix-tbody .matrix-cell').first()).not.toHaveClass(/cell-ingest-anim/);
  await expect(page.locator('#matrix-progress-spinner')).toBeHidden();
  await expect(matrixWrap.locator('#matrix-progress-msg')).toContainText('대조 완료');
  await expect(matrixWrap.locator('#matrix-progress-msg')).not.toContainText('준비 중');

  // Verify openHeatmapMatrix on completed document shows completed message and hides spinner
  await page.evaluate(() => {
    window.latestHeatmapMatrix = {
      rows: ['완료 엔티티'],
      cols: ['완료 후보'],
      matrix: [[0.95]],
    };
    window.latestHeatmapDocTitle = '완료된 문서';
    window.openHeatmapMatrix();
  });
  await expect(matrixWrap).toBeVisible();
  await expect(page.locator('#matrix-progress-spinner')).toBeHidden();
  await expect(matrixWrap.locator('#matrix-progress-msg')).toContainText('대조 완료');
  await expect(matrixWrap.locator('#matrix-progress-msg')).not.toContainText('준비 중');

  // 5. Test switching back to graph via '📊 그래프' button
  const graphBtn = matrixWrap.locator('button:has-text("그래프")').first();
  await graphBtn.click();
  await expect(matrixWrap).toBeHidden();
  await expect(page.locator('#netwrap')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('accessing Claire Bible during active ingest boots directly into heatmap matrix with graph suspended', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });

  let ingestActive = true;
  // Intercept /stats to simulate ongoing ingest on startup
  await page.route('**/stats', async (route) => {
    if (ingestActive) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          documents: 5,
          entities: 42,
          relations: 68,
          theme_id: 0,
          theme_label: '기본 지식베이스',
          ingesting: true,
          active_ingest: {
            active: true,
            payload: 'https://blog.kakaopay.com/post/tech-insight',
            source: 'telegram',
            theme_id: 0,
            theme_label: '기본 지식베이스',
            focus: '대용량 트래픽 분산',
            title: '카카오페이 기술 블로그 (대용량 트래픽 아키텍처)',
            stage: 'init',
            msg: '원문 분석 및 엔티티 대조 준비 중…',
            heatmap_matrix: null,
          },
        }),
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          documents: 6,
          entities: 48,
          relations: 75,
          theme_id: 0,
          theme_label: '기본 지식베이스',
          ingesting: false,
          active_ingest: {
            active: false,
            stage: 'done',
            title: '카카오페이 기술 블로그 (대용량 트래픽 아키텍처)',
            msg: '적재 및 대조 완료',
            result: {
              document_id: 'doc_kakaopay',
              title: '카카오페이 기술 블로그 (대용량 트래픽 아키텍처)',
              entities_created: 6,
              entities_linked: 3,
              relations_added: 7,
              heatmap_matrix: {
                rows: ['분산 트래픽', 'Kafka'],
                cols: ['엔진', '데이터베이스'],
                matrix: [[0.96, 0.4], [0.3, 0.88]],
                threshold_auto_merge: 0.93,
                threshold_borderline: 0.72,
              },
            },
          },
        }),
      });
    }
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });

  // Verify that graph is immediately suspended and NOT rendered
  const matrixWrap = page.locator('#matrixwrap');
  await expect(matrixWrap).toBeVisible();
  await expect(page.locator('#netwrap')).toBeHidden();

  // Verify graphSuspended flag
  const isSuspended = await page.evaluate(() => window.claireDebug?.graphSuspended);
  expect(isSuspended).toBe(true);

  // Verify that matrix shows active ingestion info
  await expect(matrixWrap.locator('#matrix-title')).toContainText('지식 대조 히트맵 매트릭스');
  await expect(matrixWrap.locator('#matrix-target-doc')).toContainText('카카오페이 기술 블로그');
  await expect(page.locator('#matrix-progress-spinner')).toBeVisible();

  // Now simulate ingest completing on the next poll
  ingestActive = false;
  await page.evaluate(() => window.claireDebug?.pollForUpdates());

  // Verify completed state is reflected in matrixview
  await expect(page.locator('#matrix-progress-spinner')).toBeHidden();
  await expect(matrixWrap.locator('#matrix-progress-msg')).toContainText('대조 및 적재 완료');

  // Graph remains suspended until user chooses to switch
  await expect(matrixWrap).toBeVisible();
  await expect(page.locator('#netwrap')).toBeHidden();

  // Click '📊 그래프' to resume graph
  await matrixWrap.locator('button:has-text("그래프")').first().click();
  await expect(matrixWrap).toBeHidden();
  await expect(page.locator('#netwrap')).toBeVisible();

  const isResumed = await page.evaluate(() => window.claireDebug?.graphSuspended);
  expect(isResumed).toBe(false);

  expect(pageErrors).toEqual([]);
});

test('telegram bot ingest dynamically suspends active graph and displays heatmap matrix in connected browser', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });

  // 1. Initial normal boot
  await waitForClaire(page);
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect(page.locator('#matrixwrap')).toBeHidden();
  expect(await page.evaluate(() => window.claireDebug?.graphSuspended)).toBe(false);

  // 2. Telegram bot initiates ingest: /stats returns active ingest
  await page.route('**/stats', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        documents: 10,
        entities: 90,
        relations: 140,
        ingesting: true,
        active_ingest: {
          active: true,
          payload: 'https://example.com/telegram-doc',
          source: 'telegram',
          title: '텔레그램 봇 인제스트 문서',
          stage: 'heatmap_matrix',
          msg: '실시간 엔티티 대조 중…',
          heatmap_matrix: {
            rows: ['신규 엔티티 X'],
            cols: ['기존 엔티티 Y'],
            matrix: [[0.95]],
            threshold_auto_merge: 0.93,
            threshold_borderline: 0.72,
          },
        },
      }),
    });
  });

  // Trigger poll
  await page.evaluate(() => window.claireDebug?.pollForUpdates());

  // 3. Verify graph is suspended and hidden, matrix view is active
  await expect(page.locator('#netwrap')).toBeHidden();
  const matrixWrap = page.locator('#matrixwrap');
  await expect(matrixWrap).toBeVisible();
  expect(await page.evaluate(() => window.claireDebug?.graphSuspended)).toBe(true);
  await expect(matrixWrap.locator('#matrix-target-doc')).toContainText('텔레그램 봇 인제스트 문서');
  await expect(matrixWrap.locator('#matrix-progress-msg')).toHaveText('실시간 엔티티 대조 중…');

  // 4. Click '📊 그래프' to resume
  await matrixWrap.locator('button:has-text("그래프")').first().click();
  await expect(matrixWrap).toBeHidden();
  await expect(page.locator('#netwrap')).toBeVisible();
  expect(await page.evaluate(() => window.claireDebug?.graphSuspended)).toBe(false);

  expect(pageErrors).toEqual([]);
});

test('decision stream button placed in common header tabs and displays in center view with 30-item pagination', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });

  // Mock 60 decisions to test 30-item initial display + infinite scroll (+10)
  const mockDecisions = Array.from({ length: 60 }, (_, i) => ({
    entity: `Entity_${i + 1}`,
    decision: i % 2 === 0 ? 'MERGE' : 'CREATE_NEW',
    candidate: i % 2 === 0 ? `Target_${i + 1}` : null,
    score: i % 2 === 0 ? 0.95 : null,
    stage: 'exact_match',
    reason: `Automated test resolution decision #${i + 1}`,
    document_id: 'doc_1',
    document_title: '테스트 문서',
    timestamp: 1700000000 - i * 60,
  }));

  await page.route('**/resolution/decisions*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ decisions: mockDecisions, count: mockDecisions.length }),
    });
  });

  await waitForClaire(page);

  // 1. Verify navigation buttons in common header tabs: 본문, 프레젠테이션, 그래프, 의사결정 스트림 placed in order
  const centerTabs = page.locator('#centertabs .center-tab-btn');
  await expect(centerTabs.nth(0)).toContainText('본문');
  await expect(centerTabs.nth(1)).toContainText('프레젠테이션');
  await expect(centerTabs.nth(2)).toContainText('그래프');
  await expect(centerTabs.nth(3)).toContainText('의사결정 스트림');

  // 2. Click '의사결정 스트림' button
  await centerTabs.nth(3).click();

  // 3. Verify center view switched to stream
  const streamWrap = page.locator('#streamwrap');
  await expect(streamWrap).toBeVisible();
  await expect(page.locator('#netwrap')).toBeHidden();
  await expect(page.locator('#reader')).toBeHidden();

  // 4. Verify initial load is limited to 30 items
  const cardList = streamWrap.locator('#stream-card-list .decision-card');
  await expect(cardList).toHaveCount(30);

  // 5. Scroll to bottom of container to load 10 more items
  await streamWrap.locator('#stream-scroll-container').evaluate((el) => {
    el.scrollTop = el.scrollHeight;
    el.dispatchEvent(new Event('scroll'));
  });

  // Verify now 40 items displayed
  await expect(cardList).toHaveCount(40);

  // 6. Test returning to graph via center view nav
  await page.locator('#centertab-graph').click();
  await expect(streamWrap).toBeHidden();
  await expect(page.locator('#netwrap')).toBeVisible();

  expect(pageErrors).toEqual([]);
});
