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

test('mobile primary tabs keep document navigation on the graph', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  const tabs = page.locator('#worktabs button');
  await expect(tabs).toHaveCount(4);
  await expect(page.locator('#tab-docs')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#morebtn')).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();
  await expect(page.locator('#netwrap')).toBeHidden();
  await expect(page.locator('#detailpane')).toBeHidden();
  await expect(page.locator('#detailpane')).toHaveAttribute('aria-hidden', 'true');
  expect(await page.locator('#detailpane').evaluate(element => element.inert)).toBe(true);

  for (const locator of [
    page.locator('#tab-docs'),
    page.locator('#tab-graph'),
    page.locator('#tab-search'),
    page.locator('#tab-menu'),
  ]) {
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    expect(box.height).toBeGreaterThanOrEqual(44);
  }

  // 메뉴 탭 열기 및 지식 그래프 보기 버튼으로 그래프 진입
  await page.locator('#tab-menu').click();
  await expect(page.locator('#detailpane')).toBeVisible();
  await page.locator('#opengraphbtn').click();
  await expect(page.locator('#netwrap')).toBeVisible();
  const graphDocNav = page.locator('#graphdocnav');
  await expect(graphDocNav).toBeVisible();
  await expect(page.locator('#graphdocprev')).toBeEnabled();
  await expect(page.locator('#graphdocnext')).toBeEnabled();
  for (const locator of [
    page.locator('#graphdocprev'),
    page.locator('#graphdocpick'),
    page.locator('#graphdocnext'),
  ]) {
    const box = await locator.boundingBox();
    expect(box).not.toBeNull();
    expect(box.height).toBeGreaterThanOrEqual(44);
  }
  await page.locator('#graphdocpick').click();
  await expect(page.locator('#graphdocmenu')).toBeVisible();
  await expect(page.locator('#graphdocpick')).toHaveAttribute('aria-expanded', 'true');
  await expect(page.locator('#graphdocmenu')).toHaveAttribute('aria-hidden', 'false');
  expect(await page.locator('#graphdocmenu').evaluate(element => element.inert)).toBe(false);
  await expect(page.locator('#graphdocq')).toBeFocused();
  expect(await page.locator('.graphdocoption').count()).toBeGreaterThan(1);
  await page.locator('#graphdocq').fill('__no_such_document__');
  await expect(page.locator('#graphdocempty')).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('#graphdocmenu')).toBeHidden();
  await expect(page.locator('#graphdocmenu')).toHaveAttribute('aria-hidden', 'true');
  expect(await page.locator('#graphdocmenu').evaluate(element => element.inert)).toBe(true);
  await expect(page.locator('#graphdocpick')).toBeFocused();
  const canvas = page.locator('#net canvas').first();
  await expect(canvas).toBeVisible();
  const canvasBox = await canvas.boundingBox();
  expect(canvasBox.width).toBeGreaterThan(200);
  expect(canvasBox.height).toBeGreaterThan(300);

  const zoomButtons = page.locator('#zoomctl button');
  await expect(zoomButtons).toHaveCount(4);
  for (let i = 0; i < 4; i += 1) {
    const box = await zoomButtons.nth(i).boundingBox();
    expect(box.width).toBeGreaterThanOrEqual(44);
    expect(box.height).toBeGreaterThanOrEqual(44);
  }
  const scaleBeforeZoom = await page.evaluate(() => window.claireDebug.scale);
  await zoomButtons.first().click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.scale),
  ).toBeGreaterThan(scaleBeforeZoom);
  await page.waitForTimeout(250);
  const camera = await page.evaluate(() => ({
    scale: window.claireDebug.scale,
    position: window.claireDebug.viewpos,
  }));

  await page.locator('#tab-docs').click();
  await page.locator('#tab-graph').click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.scale),
  ).toBeCloseTo(camera.scale, 4);
  const position = await page.evaluate(() => window.claireDebug.viewpos);
  expect(position.x).toBeCloseTo(camera.position.x, 3);
  expect(position.y).toBeCloseTo(camera.position.y, 3);

  // 모바일에서 자료 탭하면 크게 읽기 팝업 호출
  await page.locator('#tab-docs').click();
  await page.locator('.docitem').first().click();
  const reader = page.getByRole('dialog');
  await expect(reader).toBeVisible();
  await expect(reader).toHaveAttribute('aria-modal', 'true');
  expect(await page.locator('body').evaluate(body => body.classList.contains('reader-open'))).toBe(true);
  // 모바일에서 하단 바 그래프 탭(📊) 누르면 본문 읽기 팝업이 닫히고 그래프 화면으로 전환
  await page.locator('#tab-graph').click();
  await expect(reader).toBeHidden();
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect(page.locator('#detailpane')).toBeHidden();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activeDoc),
  ).not.toBeNull();
  const firstActiveDoc = await page.evaluate(() => window.claireDebug.activeDoc);
  await expect(page.locator('.docitem.active')).toHaveCount(1);
  await expect(page.locator('#graphdocprev')).toBeEnabled();
  await expect(page.locator('#graphdocnext')).toBeEnabled();
  await expect(page.locator('#graphdoclabel')).not.toHaveText('전체 그래프');

  await page.locator('#graphdocnext').click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activeDoc),
  ).not.toBe(firstActiveDoc);
  await expect(page.getByRole('tab', { name: '그래프' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#detailpane')).toBeHidden();
  await expect(page.getByRole('dialog', { name: '그래프에서 볼 자료 선택' })).toBeHidden();
  await expect(page.locator('#reader')).toBeHidden();

  await page.locator('#graphdocprev').click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activeDoc),
  ).toBe(firstActiveDoc);

  await page.locator('#graphdocpick').click();
  const directOption = page.locator('.graphdocoption').nth(1);
  const directDocId = await directOption.getAttribute('data-graph-doc');
  await directOption.click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activeDoc),
  ).toBe(directDocId);
  await expect(page.locator('#graphdocmenu')).toBeHidden();

  await page.locator('#graphdocpick').click();
  await page.locator('#graphdocall').click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activeDoc),
  ).toBeNull();
  await expect(page.locator('#graphdoclabel')).toHaveText('전체 그래프');
  await expect(page.locator('#graphdocprev')).toBeDisabled();
  await expect(page.locator('#graphdocnext')).toBeDisabled();
  expect(await page.evaluate(() => window.claireDebug.activePane)).toBe('graph');
  await page.waitForTimeout(600);

  const graphCamera = await page.evaluate(() => ({
    scale: window.claireDebug.scale,
    position: window.claireDebug.viewpos,
  }));
  const point = await page.evaluate(() => {
    const box = document.getElementById('net').getBoundingClientRect();
    return window.claireDebug.visibleNodePoints().find(
      item => item.x > 20 && item.y > 20 && item.x < box.width - 70 && item.y < box.height - 20,
    ) || null;
  });
  expect(point).not.toBeNull();
  await page.locator('#net').click({ position: { x: Math.round(point.x), y: Math.round(point.y) } });
  await expect(page.locator('#detailpane')).toBeVisible();
  await expect(page.locator('#panel h2')).toBeVisible();
  expect(await page.evaluate(() => window.claireDebug.activePane)).toBe('graph');
  const detailClose = page.locator('#detailclose');
  const detailCloseBox = await detailClose.boundingBox();
  expect(detailCloseBox.width).toBeGreaterThanOrEqual(44);
  expect(detailCloseBox.height).toBeGreaterThanOrEqual(44);
  await page.keyboard.press('Escape');
  await expect(page.locator('#detailpane')).toBeHidden();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.detailOpen),
  ).toBe(false);
  const graphCameraAfter = await page.evaluate(() => ({
    scale: window.claireDebug.scale,
    position: window.claireDebug.viewpos,
  }));
  expect(graphCameraAfter.scale).toBeGreaterThan(0);
  expect(typeof graphCameraAfter.position.x).toBe('number');
  expect(typeof graphCameraAfter.position.y).toBe('number');
  expect(pageErrors).toEqual([]);
});

test('tablet and desktop layouts do not squeeze the graph into three fixed columns', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);
  await expect(page.locator('#worktabs')).toBeVisible();
  await expect(page.locator('#morebtn')).toBeHidden();
  await expect(page.locator('#graphdocnav')).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();
  await page.locator('#tab-graph').click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activePane),
  ).toBe('graph');
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect.poll(async () => {
    const box = await page.locator('#netwrap').boundingBox();
    return box ? box.width : 0;
  }).toBeGreaterThan(600);
  await expect(page.locator('#detailpane')).toBeHidden();

  await page.locator('#tab-menu').click();
  await expect(page.locator('#moremenu')).toBeVisible();
  await expect(page.locator('#authstate')).toContainText('익명 읽기전용');
  await expect(page.locator('#searchkind')).toHaveText('Full-Text Search');
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

test('mobile bottom bar returns to doc list when switching from search tab to docs tab', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Initially on docs tab with document list rendered
  await expect(page.locator('#tab-docs')).toHaveAttribute('aria-selected', 'true');
  const docItems = page.locator('#doclist .docitem');
  await expect(docItems.first()).toBeVisible();
  const initialDocCount = await docItems.count();
  expect(initialDocCount).toBeGreaterThan(0);

  // 2. Click search tab (검색 단추)
  await page.locator('#tab-search').click();
  await expect(page.locator('#docq')).toBeFocused();
  await expect(page.locator('#doclist')).toContainText('검색어를 입력하세요');
  await expect(page.locator('#doclist .docitem')).toHaveCount(0);

  // 3. User types a query
  await page.locator('#docq').fill('테스트');

  // 4. Click docs tab (자료 단추) to return
  await page.locator('#tab-docs').click();
  await expect(page.locator('#tab-docs')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#docq')).toHaveValue('');
  await expect(page.locator('#doclist .docitem')).toHaveCount(initialDocCount);
  await expect(page.locator('#doclist .docitem').first()).toBeVisible();

  expect(pageErrors).toEqual([]);
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

  const actionBtn = page.locator('#opengraphbtn');
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

  // 1. Initial state: on docs tab, no modal open
  await expect(page.locator('#tab-docs')).toHaveAttribute('aria-selected', 'true');
  const reader = page.locator('#reader');
  await expect(reader).toBeHidden();

  // 2. Click document item to open reader modal on mobile
  await page.locator('.docitem').first().click();
  await expect(reader).toBeVisible();
  await expect(reader).toHaveAttribute('aria-modal', 'true');
  expect(await page.locator('body').evaluate(body => body.classList.contains('reader-open'))).toBe(true);

  // 3. Trigger browser Back (e.g. mobile OS back gesture / button)
  await page.goBack();

  // 4. Verify reader modal closes and user remains on docs list
  await expect(reader).toBeHidden();
  expect(await page.locator('body').evaluate(body => body.classList.contains('reader-open'))).toBe(false);
  await expect(page.locator('#tab-docs')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#docs')).toBeVisible();

  // 5. Open drawer menu
  await page.locator('#tab-menu').click();
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();

  // 6. Trigger browser Back -> drawer closes
  await page.goBack();
  await expect(detailPane).toBeHidden();
  await expect(page.locator('#tab-docs')).toHaveAttribute('aria-selected', 'true');

  // 7. Switch tab to Graph
  await page.locator('#tab-graph').click();
  await expect(page.locator('#tab-graph')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#netwrap')).toBeVisible();

  // 8. Trigger browser Back -> returns to Docs tab
  await page.goBack();
  await expect(page.locator('#tab-docs')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#docs')).toBeVisible();

  // 9. Open reader modal and close via close button (✕)
  await page.locator('.docitem').first().click();
  await expect(reader).toBeVisible();
  await page.locator('#reader .rclose').click();
  await expect(reader).toBeHidden();

  expect(pageErrors).toEqual([]);
});

test('mobile reader allows opening hamburger menu with detailpane and backdrop above reader', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Open reader modal by clicking document
  await page.locator('.docitem').first().click();
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();
  await expect(reader).toHaveAttribute('aria-modal', 'true');

  // 2. Click hamburger menu in bottom bar (#tab-menu) while reader is open
  const menuBtn = page.locator('#tab-menu');
  await expect(menuBtn).toBeVisible();
  await menuBtn.click();

  // 3. Detailpane and backdrop are displayed over reader (z-index check)
  const detailPane = page.locator('#detailpane');
  const backdrop = page.locator('#drawerbackdrop');
  await expect(detailPane).toBeVisible();
  await expect(backdrop).toBeVisible();

  const zIndexes = await page.evaluate(() => ({
    reader: parseInt(window.getComputedStyle(document.getElementById('reader')).zIndex, 10),
    backdrop: parseInt(window.getComputedStyle(document.getElementById('drawerbackdrop')).zIndex, 10),
    drawer: parseInt(window.getComputedStyle(document.getElementById('detailpane')).zIndex, 10),
    worktabs: parseInt(window.getComputedStyle(document.getElementById('worktabs')).zIndex, 10),
  }));

  expect(zIndexes.drawer).toBeGreaterThan(zIndexes.reader);
  expect(zIndexes.backdrop).toBeGreaterThan(zIndexes.reader);
  expect(zIndexes.drawer).toBeGreaterThan(zIndexes.backdrop);

  // 4. Close drawer via close button (✕)
  await page.locator('#detailclose').click();
  await expect(detailPane).toBeHidden();
  await expect(backdrop).toBeHidden();

  // 5. Reader remains visible and active
  await expect(reader).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('mobile reader ends above bottom bar and displays text to the end without obstruction', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Open reader modal
  await page.locator('.docitem').first().click();
  const reader = page.locator('#reader');
  const worktabs = page.locator('#worktabs');
  const rbody = page.locator('#rbody');
  await expect(reader).toBeVisible();
  await expect(worktabs).toBeVisible();

  // 2. Verify reader box does not extend under worktabs and has no horizontal scrolling
  const readerBox = await reader.boundingBox();
  const worktabsBox = await worktabs.boundingBox();
  expect(readerBox).not.toBeNull();
  expect(worktabsBox).not.toBeNull();
  expect(readerBox.y + readerBox.height).toBeLessThanOrEqual(worktabsBox.y + 1);

  const rbodyMetrics = await page.evaluate(() => {
    const b = document.getElementById('rbody');
    return { clientWidth: b.clientWidth, scrollWidth: b.scrollWidth };
  });
  expect(rbodyMetrics.scrollWidth).toBeLessThanOrEqual(rbodyMetrics.clientWidth);

  // 3. Scroll rbody to bottom and verify the last text is completely visible above worktabs
  await page.evaluate(() => {
    const b = document.getElementById('rbody');
    if (b) b.scrollTop = b.scrollHeight;
  });
  await page.waitForTimeout(200);

  const lastChildState = await page.evaluate(() => {
    const b = document.getElementById('rbody');
    const lastChild = b.lastElementChild || b;
    const rect = lastChild.getBoundingClientRect();
    const wtRect = document.getElementById('worktabs').getBoundingClientRect();
    return {
      lastChildBottom: rect.bottom,
      worktabsTop: wtRect.top,
      isCompletelyAboveWorktabs: rect.bottom <= wtRect.top,
    };
  });

  expect(lastChildState.isCompletelyAboveWorktabs).toBe(true);
  expect(lastChildState.lastChildBottom).toBeLessThanOrEqual(lastChildState.worktabsTop);

  expect(pageErrors).toEqual([]);
});




