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
  await expect(tabs).toHaveCount(3);
  await expect(page.locator('#tab-docs')).toHaveAttribute('aria-selected', 'true');
  await expect(page.locator('#morebtn')).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();
  await expect(page.locator('#netwrap')).toBeHidden();
  await expect(page.locator('#detailpane')).toBeHidden();
  await expect(page.locator('#detailpane')).toHaveAttribute('aria-hidden', 'true');
  expect(await page.locator('#detailpane').evaluate(element => element.inert)).toBe(true);

  for (const locator of [
    page.locator('#tab-docs'),
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
  await expect(page.locator('#graphdocprev')).toBeDisabled();
  await expect(page.locator('#graphdocnext')).toBeDisabled();
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
  await page.locator('#tab-menu').click();
  await page.locator('#opengraphbtn').click();
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
  // 모바일에서 본문 읽기 팝업 내 그래프 버튼(📊) 누르면 그래프 화면으로 전환
  await page.locator('#reader .rgraphbtn').click();
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
  await page.waitForTimeout(900);

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
  await page.locator('#net').click({ position: { x: point.x, y: point.y } });
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
  expect(graphCameraAfter.scale).toBeCloseTo(graphCamera.scale, 4);
  expect(graphCameraAfter.position.x).toBeCloseTo(graphCamera.position.x, 3);
  expect(graphCameraAfter.position.y).toBeCloseTo(graphCamera.position.y, 3);
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
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect(page.locator('#detailpane')).toBeHidden();
  expect((await page.locator('#netwrap').boundingBox()).width).toBeGreaterThan(600);
  await page.locator('.docitem').first().evaluate(element => element.click());
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activePane),
  ).toBe('graph');
  await expect(page.locator('#detailpane')).toBeHidden();

  await page.locator('#tab-menu').click();
  await expect(page.locator('#moremenu')).toBeVisible();
  await expect(page.locator('#authstate')).toContainText('익명 읽기전용');
  await expect(page.locator('#searchkind')).toHaveText('FTS');
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

test('compact menu icon mode toggles and preserves accessibility and interaction', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Detailpane is visible and aria-hidden is false on desktop
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();
  await expect(detailPane).toHaveAttribute('aria-hidden', 'false');

  // 2. Toggle button exists in doc header
  const toggleBtn = page.locator('#docstogglebtn');
  await expect(toggleBtn).toBeVisible();

  // 3. Compact mode active when detailpane is open: docs is compact rail
  const docs = page.locator('#docs');
  await expect(docs).toBeVisible();
  const docsBox = await docs.boundingBox();
  expect(docsBox.width).toBeLessThanOrEqual(65);

  // 4. In compact mode, doc items show icon tile and preserve tooltips/aria-labels
  const firstDoc = page.locator('.docitem').first();
  await expect(firstDoc).toBeVisible();
  await expect(firstDoc).toHaveAttribute('title');
  await expect(firstDoc).toHaveAttribute('aria-label');

  // 5. Clicking toggle button expands to full width
  await toggleBtn.click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.docsCompact),
  ).toBe(false);
  const expandedBox = await docs.boundingBox();
  expect(expandedBox.width).toBeGreaterThanOrEqual(240);
  await expect(page.locator('#docq')).toBeVisible();

  // 6. Clicking toggle button again restores compact mode
  await toggleBtn.click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.docsCompact),
  ).toBe(true);
  const recompactBox = await docs.boundingBox();
  expect(recompactBox.width).toBeLessThanOrEqual(65);

  // 7. Clicking doc item in compact mode selects document
  await firstDoc.click();
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.activeDoc),
  ).not.toBeNull();

  expect(pageErrors).toEqual([]);
});

