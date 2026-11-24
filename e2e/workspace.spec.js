const { test, expect } = require('@playwright/test');

// The PWA service worker (/sw.js) sends each request of the page again from its own context. page.route() does not see those requests, so a mocked answer is lost after the worker starts. The other specs block the worker in the same way.
test.use({ serviceWorkers: 'block' });

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

test('mobile primary view displays the bottom view bar and the graph, and the header bar has no context button', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. The bottom view bar shows. A launch on a narrow screen starts in the reader, thus the graph button opens the graph.
  await expect(page.locator('#botnav')).toBeVisible();
  await expect(page.locator('#botnav-list')).toBeVisible();
  await expect(page.locator('#botnav-graph')).toBeVisible();
  await expect(page.locator('#botnav-reader')).toBeVisible();
  await page.locator('#botnav-graph').click();
  await expect(page.locator('#netwrap')).toBeVisible();

  // 2. 바텀 바 및 단추 제거 확인
  await expect(page.locator('#worktabs')).toBeHidden();

  // 3. The header bar has the theme switch and the hamburger menu, but no context button: the context panel has its one close button in its own head.
  const themebtn = page.locator('#themebtn');
  const hamburgerBtn = page.locator('#hamburger-btn');
  await expect(themebtn).toBeVisible();
  await expect(hamburgerBtn).toBeVisible();
  await expect(page.locator('#bar #ctxbtn, #bar #morebtn')).toHaveCount(0);
  const themeBox = await themebtn.boundingBox();
  const hamburgerBox = await hamburgerBtn.boundingBox();
  expect(themeBox).not.toBeNull();
  expect(hamburgerBox).not.toBeNull();
  expect(hamburgerBox.x).toBeGreaterThan(themeBox.x);

  // 4. The open button of the graph canvas opens the context sheet and hides; the close button of the sheet closes it.
  const graphCtxBtn = page.locator('#gctxbtn');
  await expect(graphCtxBtn).toBeVisible();
  await graphCtxBtn.click();
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();
  await expect(graphCtxBtn).toBeHidden();
  await page.locator('#detailclose').click();
  await expect(detailPane).toBeHidden();
  await expect(graphCtxBtn).toBeVisible();
  await expect(page.locator('#botnav')).toBeVisible();

  // 5. 센터탭에서 본문 읽기(reader) 본문 영역 표시 (팝업 모달이 아닌 중앙 작업 영역)
  const tabReader = page.locator('#botnav-reader');
  await tabReader.click();

  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();
  await expect(page.locator('#botnav')).toBeVisible();

  // 6. The document head bar has no close button. Its context button only opens the context sheet and hides while the sheet is open.
  await expect(page.locator('#reader .rclose')).toHaveCount(0);
  await page.locator('#rctxbtn').click();
  await expect(page.locator('#detailpane')).toBeVisible();
  await expect(page.locator('#rctxbtn')).toBeHidden();
  // The Context item of the bottom bar shows the open sheet; it does not close it.
  await expect(page.locator('#botnav-context')).toHaveAttribute('aria-expanded', 'true');
  await page.locator('#botnav-context').click();
  await expect(page.locator('#detailpane')).toBeVisible();
  await page.locator('#detailclose').click();
  await expect(page.locator('#detailpane')).toBeHidden();
  await expect(page.locator('#rctxbtn')).toBeVisible();

  // 7. 센터탭에서 프레젠테이션 및 그래프 전환
  await page.locator('#botnav-graph').click();
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect(reader).toBeHidden();

  expect(pageErrors).toEqual([]);
});

test('tablet and desktop layouts do not squeeze the graph into three fixed columns', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 768 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);
  await expect(page.locator('#worktabs')).toBeHidden();
  await expect(page.locator('#bar #ctxbtn, #bar #morebtn')).toHaveCount(0);
  await expect(page.locator('#graphdocnav')).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page.locator('#homewrap')).toBeVisible();
  await page.locator('#centertab-graph').click();
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect.poll(async () => {
    const box = await page.locator('#netwrap').boundingBox();
    return box ? box.width : 0;
  }).toBeGreaterThan(600);
  await expect(page.locator('#detailpane')).toBeHidden();

  await page.locator('#gctxbtn').click();
  await expect(page.locator('#detailpane')).toBeVisible();
  await expect(page.locator('#gctxbtn')).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.locator('#detailpane')).toBeHidden();
  await expect(page.locator('#gctxbtn')).toBeVisible();

  await expect(page.locator('#authstate')).toHaveCount(0);
  await expect(page.locator('#addbtn')).toBeHidden();
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#pop-hamburger-menu')).toBeVisible();
  await expect(page.locator('#synthbtn')).toBeHidden();
  await expect(page.locator('#dedupbtn')).toHaveCount(1);
  await expect(page.locator('#dedupbtn')).toBeHidden();
  await expect(page.locator('#purgebtn')).toHaveCount(1);
  await expect(page.locator('#purgebtn')).toBeHidden();
  await expect(page.locator('#menu-logout-form')).toBeHidden();
  await page.keyboard.press('Escape');
  await expect(page.locator('#pop-hamburger-menu')).toBeHidden();

  await page.setViewportSize({ width: 1600, height: 900 });
  await expectNoHorizontalOverflow(page);
  await expect(page.locator('#worktabs')).toBeHidden();
  await expect(page.locator('#docs')).toBeVisible();
  await expect(page.locator('#netwrap')).toBeVisible();
  await expect(page.locator('#detailpane')).toBeVisible();
});


test('context panel stays closed without selection, opens on demand, and has one close button on desktop', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Without a selection, #detailpane is collapsed (ctx-closed). The header bar has no context button.
  const detailPane = page.locator('#detailpane');
  const closeBtn = page.locator('#detailclose');
  const readerCtxBtn = page.locator('#rctxbtn');
  await expect(page.locator('body')).toHaveClass(/ctx-closed/);
  await expect(detailPane).toHaveAttribute('aria-hidden', 'true');
  await expect(page.locator('#bar #ctxbtn, #bar #morebtn')).toHaveCount(0);

  // 2. Open a document in reader -> #detailpane opens on demand (>= 300px). While it is open, its own close button is the only visible control for it.
  await page.locator('.docitem').first().click();
  await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
  await expect(detailPane).toBeVisible();
  await expect(detailPane).toHaveAttribute('aria-hidden', 'false');
  await expect.poll(async () => {
    const box = await detailPane.boundingBox();
    return box ? box.width : 0;
  }).toBeGreaterThanOrEqual(300);
  await expect(closeBtn).toBeVisible();
  await expect(readerCtxBtn).toBeHidden();

  // 3. The close button collapses #detailpane to zero width. The focus goes to the open button of the document head bar.
  await closeBtn.click();
  await expect(page.locator('body')).toHaveClass(/ctx-closed/);
  await expect.poll(async () => {
    const box = await detailPane.boundingBox();
    return box ? box.width : 999;
  }).toBeLessThanOrEqual(1);
  await expect(readerCtxBtn).toBeVisible();
  await expect(readerCtxBtn).toBeFocused();

  // 4. The open button restores the full width, hides, and moves the focus to the close button.
  await readerCtxBtn.press('Enter');
  await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
  await expect.poll(async () => {
    const box = await detailPane.boundingBox();
    return box ? box.width : 0;
  }).toBeGreaterThanOrEqual(300);
  await expect(readerCtxBtn).toBeHidden();
  await expect(closeBtn).toBeFocused();

  // 5. The graph view has its own open button on the canvas, with the same rule.
  await closeBtn.click();
  await page.locator('#centertab-graph').click();
  const graphCtxBtn = page.locator('#gctxbtn');
  await expect(graphCtxBtn).toBeVisible();
  await graphCtxBtn.click();
  await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
  await expect(graphCtxBtn).toBeHidden();
  await expect(page.locator('#ctx-insights')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('inspecting node on desktop displays details without backdrop dimming or click blocking', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Context panel starts closed without selection
  await expect(page.locator('body')).toHaveClass(/ctx-closed/);

  // 2. Select document, collapse panel, and switch to graph
  // The graph view shows the entity list. The document list is in the reader view.
  await page.locator('#centertab-reader').click();
  await page.locator('.docitem').first().evaluate(element => element.click());
  await page.locator('#detailclose').click();
  await expect(page.locator('body')).toHaveClass(/ctx-closed/);
  await page.evaluate(() => setCenterView('graph'));
  await expect.poll(
    () => page.evaluate(() => window.claireDebug.centerView),
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
    () => page.evaluate(() => window.claireDebug.contextOpen),
  ).toBe(true);

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

test('graph engine paints marks and labels through WebGPU or the forced Canvas2D fallback', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  const paintedPixels = selector => page.evaluate(sel => {
    const canvas = document.querySelector(sel);
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
    let count = 0;
    for (let i = 3; i < data.length; i += 4) if (data[i] > 0) count++;
    return count;
  }, selector);

  // 1. 기본(auto): WebGPU 가 없거나 실패하면 Canvas2D 로 내려가며, 어느 쪽이든 라벨 오버레이를 그린다.
  await waitForClaire(page);
  await page.locator('#centertab-graph').click();
  const info = await page.evaluate(() => window.claireDebug.graphRenderer);
  expect(['webgpu', 'canvas2d']).toContain(info.backend);
  await expect(page.locator('#net canvas.claire-graph-marks')).toHaveCount(1);
  await expect(page.locator('#net canvas.claire-graph-labels')).toHaveCount(1);
  await expect.poll(() => paintedPixels('#net canvas.claire-graph-labels')).toBeGreaterThan(20);

  // 2. ?graphBackend=canvas2d 로 강제하면 같은 프레임을 2D 경로로 그린다.
  await page.goto('/?graphBackend=canvas2d', { waitUntil: 'domcontentloaded' });
  await page.locator('#centertab-graph').click();
  await expect.poll(() => page.evaluate(() => window.claireDebug?.graphRenderer?.backend)).toBe('canvas2d');
  await expect.poll(() => paintedPixels('#net canvas.claire-graph-marks')).toBeGreaterThan(20);

  expect(pageErrors).toEqual([]);
});

test('graph engine lays hubs out as spoke stars and springs a flung leaf back to its tether', async ({ page }) => {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window.ClaireGraph && window.ClaireGraph.Network);
  const result = await page.evaluate(async () => {
    const G = window.ClaireGraph;
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;left:0;top:0;width:600px;height:400px;z-index:9999;background:#fff';
    document.body.appendChild(host);
    const nodes = [], edges = [];
    for (let h = 0; h < 3; h++) {
      nodes.push({ id: 'h' + h, label: 'Hub ' + h, group: 'Tool', size: 6 });
      for (let k = 0; k < 8; k++) {
        const id = 'h' + h + 'c' + k;
        nodes.push({ id, label: id, group: 'Concept', size: 3 });
        edges.push({ id: 'e' + id, from: 'h' + h, to: id });
      }
    }
    const net = new G.Network(host, { nodes: new G.DataSet(nodes), edges: new G.DataSet(edges) }, { renderer: { backend: 'canvas2d' } });
    const sim = net.sim;
    const leaf = net._index.get('h0c0'), hub = net._index.get('h0');
    const dist = () => Math.hypot(sim.x[leaf] - sim.x[hub], sim.y[leaf] - sim.y[hub]);
    await new Promise(resolve => setTimeout(resolve, 300));
    const tether = sim.tether[leaf], before = dist();
    sim.dragTo(leaf, sim.x[hub] + 400, sim.y[hub], 16);
    sim.release(leaf, 16, false);
    const stretched = dist();
    await new Promise(resolve => setTimeout(resolve, 1500));
    const after = dist();
    const out = { roots: sim.h.roots.map(i => net._ids[i]).sort(), tether, before, stretched, after, backend: net.backend };
    net.destroy();
    host.remove();
    return out;
  });
  expect(result.backend).toBe('canvas2d');
  expect(result.roots).toEqual(['h0', 'h1', 'h2']);
  expect(Math.abs(result.before - result.tether)).toBeLessThan(2);
  expect(result.stretched).toBeGreaterThan(result.tether * 2);
  expect(Math.abs(result.after - result.tether)).toBeLessThan(3);
});

test('mobile history back navigation closes modal and returns to previous view without exiting', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. A launch on a narrow screen starts in the reader. The test starts from the graph view with no modal.
  await expect(page.locator('#botnav')).toBeVisible();
  await page.locator('#botnav-graph').click();
  const reader = page.locator('#reader');
  await expect(reader).toBeHidden();

  // 2. 왼쪽 드로어(#botnav-list) 열기
  await page.locator('#botnav-list').click();
  const docsDrawer = page.locator('#docs');
  await expect(docsDrawer).toBeVisible();
  expect(await page.locator('body').evaluate(body => body.classList.contains('docdrawer-open'))).toBe(true);

  // 3. 브라우저 뒤로가기 실행 -> 드로어 닫히고 센터 뷰 유지
  await page.goBack();
  expect(await page.locator('body').evaluate(body => body.classList.contains('docdrawer-open'))).toBe(false);
  await expect(page.locator('#botnav')).toBeVisible();

  // 4. Open the context sheet with the Context item of the bottom bar.
  await page.locator('#botnav-context').click();
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();

  // 5. 브라우저 뒤로가기 실행 -> 드로어 닫히고 센터 뷰 유지
  await page.goBack();
  await expect(detailPane).toBeHidden();
  await expect(page.locator('#botnav')).toBeVisible();

  // 6. Open the reader, then leave it with the graph button of the bottom bar.
  await page.locator('#botnav-reader').click();
  await expect(reader).toBeVisible();
  await page.locator('#botnav-graph').click();
  await expect(reader).toBeHidden();
  await expect(page.locator('#botnav')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('mobile reader can be opened and closed, and drawer menu opens above document list', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. 센터탭에서 본문 클릭하여 본문 영역 표시
  await page.locator('#botnav-reader').click();
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();
  await expect(page.locator('#botnav')).toBeVisible();

  // 2. Leave the reader with the graph button of the bottom bar (the reader has no close button).
  await page.locator('#botnav-graph').click();
  await expect(reader).toBeHidden();
  await expect(page.locator('#botnav')).toBeVisible();

  // 3. Open the context sheet with the open button of the graph canvas.
  const graphCtxBtn = page.locator('#gctxbtn');
  await expect(graphCtxBtn).toBeVisible();
  await graphCtxBtn.click();

  // 4. Detailpane과 backdrop이 표시되는지 확인
  const detailPane = page.locator('#detailpane');
  const backdrop = page.locator('#drawerbackdrop');
  await expect(detailPane).toBeVisible();
  await expect(backdrop).toBeVisible();

  // 5. 닫기 단추(✕)로 드로어 닫기
  await page.locator('#detailclose').click();
  await expect(detailPane).toBeHidden();
  await expect(backdrop).toBeHidden();

  // 6. 센터 뷰가 계속 활성화되어 있는지 확인
  await expect(page.locator('#botnav')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('mobile reader extends to bottom and displays text to the end without obstruction', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. 리더 모달 열기
  await page.locator('#botnav-reader').click();
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();

  // 2. The reader box goes down to the bottom view bar (52px), with no horizontal scroll.
  const readerBox = await reader.boundingBox();
  expect(readerBox).not.toBeNull();
  expect(readerBox.y + readerBox.height).toBeGreaterThanOrEqual(790);

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
  await page.goto('/silvercord/23456789abcdefgh', { waitUntil: 'domcontentloaded' });

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

test('the entity address opens the graph with the entity selected, and the old node link redirects to it', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/entity/45678923abcdefgh', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');
  // The context panel shows the entity of the address.
  await expect(page.locator('#panel')).toContainText('허브 엔티티');
  await expect(page).toHaveURL(/\/entity\/45678923abcdefgh$/);
  // The context panel has the action "copy link" with the address of the entity. The knowledge base is public, thus it has no link access switch.
  await expect(page.locator('#node-copy-link-btn')).toHaveAttribute('data-entity-path', '/entity/45678923abcdefgh');
  await expect(page.locator('#node-copy-link-btn')).toContainText('링크 복사');
  await expect(page.locator('#node-link-access-btn')).toHaveCount(0);
  // An old link with the entity ID gives the same address.
  const old = await page.request.get('/?node=ent-hub', { maxRedirects: 0 });
  expect([302, 308]).toContain(old.status());
  await page.goto('/?node=ent-hub', { waitUntil: 'domcontentloaded' });
  await expect(page).toHaveURL(/\/entity\/45678923abcdefgh$/);
  await expect(page.locator('#panel')).toContainText('허브 엔티티');
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
  // The graph view shows the entity list. The document list is in the reader view.
  await page.locator('#centertab-reader').click();
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
  expect(shareData.path).toMatch(/^\/silvercord\/[2-9a-z]{16}$/);

  // 5. Verify reader sharebox displays the generated URL
  const shareBox = page.locator('#reader #sharebox');
  await expect(shareBox).toBeVisible();
  const shareInput = shareBox.locator('#shareurl');
  await expect(shareInput).toHaveValue(new RegExp(shareData.path + '$'));

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
  // The graph view shows the entity list. The document list is in the reader view.
  await page.locator('#centertab-reader').click();
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
  await expect(flawTag).toContainText('PDF 인코딩 결함');
  await expect(flawTag).toHaveAttribute('title', /unmapped_cid_fonts/);

  const scannedTag = reader.locator('.scanned-tag');
  await expect(scannedTag).toBeVisible();
  await expect(scannedTag).toContainText('스캔본 PDF');

  // 3. Verify the item view of the permalink (/silvercord/34567892abcdefgh) also renders these tags
  const sharedPage = await context.newPage();
  const sharedResponse = await sharedPage.goto('/silvercord/34567892abcdefgh');
  expect(sharedResponse.status()).toBe(200);

  const shareFallbackTag = sharedPage.locator('.parser-fallback-tag');
  await expect(shareFallbackTag).toBeVisible();
  await expect(shareFallbackTag).toContainText('PyPDFium2 폴백 (PyPDF)');

  const shareFlawTag = sharedPage.locator('.encoding-flaw-tag');
  await expect(shareFlawTag).toBeVisible();
  await expect(shareFlawTag).toContainText('PDF 인코딩 결함');

  const shareScannedTag = sharedPage.locator('.scanned-tag');
  await expect(shareScannedTag).toBeVisible();
  await expect(shareScannedTag).toContainText('스캔본 PDF');

  await sharedPage.close();
  expect(pageErrors).toEqual([]);
});

test('node aliases render and can be promoted to primary representative label in owner session', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });

  // 1. Authenticate as owner
  await page.goto('/?t=e2e-aliases-token-0123456789abcdef0', { waitUntil: 'domcontentloaded' });
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.authScope),
  ).toBe('owner');
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.stabilized),
  ).toBe(true);

  // 2. Select document 1 and switch to graph
  // The graph view shows the entity list. The document list is in the reader view.
  await page.locator('#centertab-reader').click();
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

  // 5. Check '엔티티 관리' button exists next to '종합에 추가' and open dropdown
  const manageBtn = panel.locator('#node-manage-btn');
  await expect(manageBtn).toBeVisible();
  await expect(manageBtn).toContainText('엔티티 관리');
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

test('provider management lives in the admin console and the root topbar shows only the console icon', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });

  // 1. Anonymous users see no console icon, and the detail menu has no manage panel at all
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.authScope),
  ).toBe('anonymous');
  await expect(page.locator('#consolelinkbtn')).toBeHidden();
  await expect(page.locator('#manage-section')).toHaveCount(0);
  await expect(page.locator('#providermanagebtn')).toHaveCount(0);

  // 2. Authenticate as owner
  await page.goto('/?t=e2e-provider-token-0123456789abcdef', { waitUntil: 'domcontentloaded' });
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.authScope),
  ).toBe('owner');
  await expect.poll(
    () => page.evaluate(() => window.claireDebug?.stabilized),
  ).toBe(true);

  // 3. The admin owner sees right-to-left icons (#hamburger-btn -> #syndlink -> #homelinkbtn) and finds the 🛠️ console icon inside the hamburger menu
  const endOrder = await page.evaluate(() => ['#themebtn', '#homelinkbtn', '#syndlink', '#hamburger-btn'].map((s) => document.querySelector(s).getBoundingClientRect().left));
  for (let i = 1; i < endOrder.length; i += 1) {
    expect(endOrder[i]).toBeGreaterThan(endOrder[i - 1]);
  }
  await page.locator('#hamburger-btn').click();
  const consoleBtn = page.locator('#consolelinkbtn');
  await expect(consoleBtn).toBeVisible();
  await expect(consoleBtn).toHaveAttribute('href', '/admin');
  await expect(consoleBtn.locator('[data-icon]')).toHaveAttribute('data-icon', 'wrench');
  await expect(consoleBtn.locator('svg.lineicon')).toHaveCount(1);
  await expect(consoleBtn).toHaveAttribute('aria-label', '관리자 콘솔');

  // 4. Follow the icon from the hamburger menu into the console, then open the provider section
  await consoleBtn.click();
  await expect(page).toHaveURL(/\/admin\/server$/);
  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });
  const detailPane = page.locator('#admin-providers-host');
  await expect(detailPane).toBeVisible();

  // 5. The provider page (PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md): what applies now first, then one form for each part. e2e/provider_settings.spec.js checks the forms and their results.
  await expect(detailPane.getByRole('heading', { level: 2, name: /지금 적용 중인 설정/ })).toBeVisible();
  // The status tab comes first; the key and the models are in the second tab.
  await expect(page.locator('#prov-tabs [role="tab"]').first()).toHaveAttribute('data-tab', 'status');
  await page.locator('#prov-tabs [role="tab"][data-tab="model"]').click();
  await expect(page.locator('#prov-gemini-model')).toBeVisible();
  expect(await page.locator('#prov-gemini-model').evaluate(el => el.tagName)).toBe('SELECT');
  await expect(page.locator('#prov-active-select')).toHaveCount(0);
  await expect(page.locator('#prov-agy-bin')).toHaveCount(0);
  await expect(page.locator('#prov-cdx-bin')).toHaveCount(0);

  // 6. The connection check of the key: the result shows next to the button.
  const checkBtn = page.locator('#prov-key-form button[value="check"]');
  await checkBtn.click();
  await expect(page.locator('#prov-key-form [data-prov-status]')).toContainText(/키|API|확인|key/i);

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
  await expect(matrixBanner).toContainText('지식 적재 대조 콕핏');

  // 7. Click '콕핏에서 보기' button
  const fullMatrixBtn = matrixBanner.locator('button:has-text("콕핏에서 보기")');
  await fullMatrixBtn.click();

  // 8. Verify #matrixwrap is displayed as center view with pure visual grid
  const matrixWrap = page.locator('#matrixwrap');
  await expect(matrixWrap).toBeVisible();
  await expect(matrixWrap.locator('#matrix-title')).toContainText('지식 적재 콕핏');
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

  // 1. Open graph view first (HW-1 boots into home), then verify graph canvas is visible and matrixwrap is hidden
  await page.locator('#centertab-graph').click();
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
  await expect(matrixWrap.locator('#matrix-title')).toContainText('지식 적재 콕핏');
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

  // 5. Test switching back to graph via '#centertab-graph' tab button
  await page.locator('#centertab-graph').click();
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
          kb_id: 0,
          theme_label: '기본 지식베이스',
          ingesting: true,
          active_ingest: {
            active: true,
            payload: 'https://blog.kakaopay.com/post/tech-insight',
            source: 'telegram',
            kb_id: 0,
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
          kb_id: 0,
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
  await expect(matrixWrap.locator('#matrix-title')).toContainText('지식 적재 콕핏');
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

  // Click '#centertab-graph' to resume graph
  await page.locator('#centertab-graph').click();
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

  // 1. Initial normal boot. The first view is the home overview (HW-1); the test opens the graph.
  await waitForClaire(page);
  await page.locator('#centertab-graph').click();
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

  // 4. Click '#centertab-graph' to resume
  await page.locator('#centertab-graph').click();
  await expect(matrixWrap).toBeHidden();
  await expect(page.locator('#netwrap')).toBeVisible();
  expect(await page.evaluate(() => window.claireDebug?.graphSuspended)).toBe(false);

  expect(pageErrors).toEqual([]);
});

test('heatmap matrix provides direct reader transition, compact headers, and refined inspect bar without rollover distortion', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await waitForClaire(page);
  await page.locator('#centertab-graph').click();
  await page.waitForSelector('#net');

  // 1. Simulate opening heatmap matrix for doc-2
  await page.evaluate(() => {
    localStorage.removeItem('doc_matrix_confirmed_doc-2');
    sessionStorage.removeItem('doc_matrix_confirmed_doc-2');
    sessionStorage.setItem('doc_matrix_doc-2', JSON.stringify({
      document_id: 'doc-2',
      rows: ['엔티티 B (매우 긴 명칭 테스트)', '엔티티 C'],
      cols: ['엔티티 A (매우 긴 기준 열)', '엔티티 B'],
      matrix: [[0.95, 0.2], [0.3, 0.88]],
      threshold_auto_merge: 0.93,
      threshold_borderline: 0.72
    }));
    window.openHeatmapMatrix('doc-2');
  });

  const matrixWrap = page.locator('#matrixwrap');
  await expect(matrixWrap).toBeVisible();

  // 2. Verify compact corner, column, and row headers
  await expect(page.locator('#matrix-thead-row .matrix-corner-header')).toBeVisible();
  await expect(page.locator('#matrix-thead-row .matrix-col-header')).toHaveCount(2);
  await expect(page.locator('#matrix-thead-row .matrix-col-label').first()).toBeVisible();
  await expect(page.locator('#matrix-tbody .matrix-row-header')).toHaveCount(2);
  await expect(page.locator('#matrix-tbody .matrix-row-label').first()).toBeVisible();

  // 3. Verify inspect bar updates on hover
  const firstCell = page.locator('#matrix-tbody .matrix-cell').first();
  await firstCell.hover();
  const inspectBar = page.locator('#matrix-inspect-text');
  await expect(inspectBar).toContainText('유사도: 0.9500');

  // 4. Verify direct reader transition link from matrix topbar doc meta
  const docLink = page.locator('#matrix-target-doc .matrix-doc-link');
  await expect(docLink).toBeVisible();
  await docLink.click();

  // 5. Verify reader is now visible and matrixwrap is hidden
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();
  await expect(matrixWrap).toBeHidden();

  // 6. Verify preview cells in matrix preview banner have .matrix-preview-cell class
  const previewBanner = page.locator('#doc-matrix-banner-doc-2');
  await expect(previewBanner).toBeVisible();
  const previewCells = previewBanner.locator('.matrix-preview-cell');
  expect(await previewCells.count()).toBeGreaterThan(0);

  expect(pageErrors).toEqual([]);
});

test('mobile view features: bottom view bar, left doc drawer, and inline reader without popup', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  // 1. Verify center tabs are single-row nowrap
  const centerTabs = page.locator('#botnav');
  await expect(centerTabs).toBeVisible();

  // 2. Verify leftmost button in center tabs is the mobile document list button
  const mobileDocBtn = page.locator('#botnav-list');
  await expect(mobileDocBtn).toBeVisible();
  const readerTabBtn = page.locator('#botnav-reader');
  const mobileDocBox = await mobileDocBtn.boundingBox();
  const readerTabBox = await readerTabBtn.boundingBox();
  expect(mobileDocBox).not.toBeNull();
  expect(readerTabBox).not.toBeNull();
  expect(mobileDocBox.x).toBeLessThan(readerTabBox.x);

  // 3. Verify graph menu does not have prev/next buttons
  await expect(page.locator('#graphdocprev')).toHaveCount(0);
  await expect(page.locator('#graphdocnext')).toHaveCount(0);

  // 4. Click mobile doc list button to open left popup drawer
  await mobileDocBtn.click();
  const docsDrawer = page.locator('#docs');
  await expect(docsDrawer).toBeVisible();
  expect(await page.locator('body').evaluate(body => body.classList.contains('docdrawer-open'))).toBe(true);

  // 5. Select a document from the drawer list
  const firstDoc = page.locator('#doclist .docitem').first();
  await expect(firstDoc).toBeVisible();
  await firstDoc.click();

  // 6. Verify doc drawer closes and reader is displayed directly inline in center pane (not popup modal)
  expect(await page.locator('body').evaluate(body => body.classList.contains('docdrawer-open'))).toBe(false);
  const reader = page.locator('#reader');
  await expect(reader).toBeVisible();
  await expect(centerTabs).toBeVisible();
  await expect(page.locator('#centerhead')).toBeVisible();

  // Verify reader positioning is relative (inline within workspace), not fixed modal
  const readerPosition = await reader.evaluate(el => window.getComputedStyle(el).position);
  expect(readerPosition).toBe('relative');

  // 7. Switching to graph via center tab works cleanly
  await page.locator('#botnav-graph').click();
  await expect(reader).toBeHidden();
  await expect(page.locator('#netwrap')).toBeVisible();

  expect(pageErrors).toEqual([]);
});

test('hidden documents list in document sidebar is scrollable when opened in owner session', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/?t=e2e-hidden-token-0123456789abcdef01', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  // The graph view shows the entity list. The document list is in the reader view.
  await page.locator('#centertab-reader').click();
  const showHiddenBtn = page.locator('#showhidden');
  await expect(showHiddenBtn).toBeVisible();
  await expect(showHiddenBtn).toContainText('숨김');
  const hiddenList = page.locator('#hiddenlist');
  await expect(hiddenList).toBeHidden();
  await showHiddenBtn.click();
  await expect(hiddenList).toBeVisible();
  await expect(showHiddenBtn).toHaveAttribute('aria-expanded', 'true');
  const overflowY = await hiddenList.evaluate(el => window.getComputedStyle(el).overflowY);
  expect(overflowY).toBe('auto');
  const { scrollHeight, clientHeight } = await hiddenList.evaluate(el => ({ scrollHeight: el.scrollHeight, clientHeight: el.clientHeight }));
  expect(scrollHeight).toBeGreaterThan(clientHeight);
  await hiddenList.evaluate(el => { el.scrollTop = 150; });
  const scrolledTop = await hiddenList.evaluate(el => el.scrollTop);
  expect(scrolledTop).toBeGreaterThanOrEqual(100);
  await showHiddenBtn.click();
  await expect(hiddenList).toBeHidden();
  await expect(showHiddenBtn).toHaveAttribute('aria-expanded', 'false');
  expect(pageErrors).toEqual([]);
});

test('WebUI regenerate button opens form and executes without client script errors', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });

  // 1. Authenticate as owner
  await page.goto('/?t=e2e-regen-token-0123456789abcdef01', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

  // 2. Open Ingest dropdown (#addbtn) and click #ingest-opt-regen
  await page.locator('#addbtn').click();
  const regenOpt = page.locator('#ingest-opt-regen');
  await expect(regenOpt).toBeVisible();
  await regenOpt.click();

  // 3. Verify regeneration modal dialog rendered
  const modal = page.locator('dialog#modal-regen');
  await expect(modal).toBeVisible();
  await expect(modal.locator('h2')).toContainText('문서 컴포넌트 재생성');
  await expect(page.locator('#regentarget')).toBeVisible();

  // 4. Fill target and click Dry-Run
  // The target is a seed document with no entity. The pattern doc_1 also matches doc-1, and a real regeneration of doc-1 removes the entities that other tests use.
  await page.locator('#regentarget').fill('doc-regen-1');
  const dryRunBtn = modal.locator('#btn-regen-dryrun');
  await expect(dryRunBtn).toBeVisible();
  await dryRunBtn.click();

  // 5. Verify dry run results or result area appears
  await expect(page.locator('#regenresult')).toBeVisible();
  // The dialog has one run button (the footer): the dry run result has no second run button.
  await expect(page.locator('#regenresult h3')).toBeVisible();
  await expect(page.locator('#regenresult button')).toHaveCount(0);

  // 6. Click '재생성 시작' and verify no uncaught reference errors occurred
  const startBtn = modal.locator('button.readbtn:has-text("재생성 시작")');
  await expect(startBtn).toBeVisible();
  await startBtn.click();

  // 7. Verify no script errors (such as pauseGraphForIngest is not defined) were thrown
  expect(pageErrors).toEqual([]);
});

test('WebUI regenerate shows a failure, not a completion, when the target result has an error', async ({ page }) => {
  // Support bundle sb_20261119_132433: the LLM call failed, but the result screen said "complete".
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  const errText = "가독 상세(detail) 생성 실패 (Error code: 400 - Unknown parameter 'thinking_config')";
  await page.route('**/regenerate-stream', async (route) => {
    const result = { dry_run: false, count: 1, targets: [{ document_id: 'doc-regen-1', title: 'Regen Failure Doc', actions: ['regenerate_detail'], error: errText }] };
    await route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: JSON.stringify({ stage: 'work', msg: 'detail' }) + '\n' + JSON.stringify({ done: true, result }) + '\n' });
  });

  await page.goto('/?t=e2e-regenfail-token-0123456789abcd', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await page.locator('#addbtn').click();
  await page.locator('#ingest-opt-regen').click();
  const modal = page.locator('dialog#modal-regen');
  await expect(modal).toBeVisible();
  await page.locator('#regentarget').fill('doc-regen-1');
  await modal.locator('#regencomp-detail').check();
  await modal.locator('button.readbtn:has-text("재생성 시작")').click();

  const panel = page.locator('#panel');
  await expect(panel.locator('h2')).toContainText('재생성 실패');
  await expect(panel).toContainText('thinking_config');
  await expect(panel).not.toContainText('컴포넌트 재생성 완료');
  expect(pageErrors).toEqual([]);
});

test('brand logo click restores default home state (degree, center view, title, URL) identical to boot', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });

  await page.route('**/stats', async (route) => {
    const res = await route.fetch();
    const json = await res.json();
    json.ingesting = false;
    json.active_jobs = [];
    json.active_ingest = null;
    await route.fulfill({ response: res, json });
  });

  // 1. Boot
  await waitForClaire(page);
  await expect(page).toHaveTitle('Claire Bible — 홈');
  const initialCenterView = await page.locator('body').getAttribute('data-center-view');
  expect(initialCenterView).toBe('home');
  const initialDeg = await page.evaluate(() => window.claireDebug?.curMinDeg);
  const initialUrl = page.url();

  // 2. Modify state: open graph, search query, change degree preset, switch center view
  await page.locator('#centertab-graph').click();
  await page.locator('#q').fill('Anthropic');
  await page.locator('.deg-preset-btn[data-deg="5"]').click();
  await page.locator('#centertab-stream').click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'stream');

  // 3. Click brand logo anchor
  const brand = page.locator('a.brand');
  await expect(brand).toBeVisible();
  await brand.click();

  // 4. Verify restored state identical to boot
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page).toHaveTitle('Claire Bible — 홈');
  await expect(page.locator('#homewrap')).toBeVisible();
  const restoredDeg = await page.evaluate(() => window.claireDebug?.curMinDeg);
  expect(restoredDeg).toBe(initialDeg);
  await expect(page.locator('#q')).toHaveValue('');
  expect(page.url()).toBe(initialUrl);
  expect(pageErrors).toEqual([]);
});

test('Unified Ingest Cockpit renders 5-stage horizontal milestone stepper on desktop and tablet and transitions from live extraction preview to heatmap tensor', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });

  let currentPhase = 'extract';
  await page.route('**/stats', async (route) => {
    const isExtract = currentPhase === 'extract';
    const jobPayload = {
      job_id: 'j_cockpit_1',
      active: true,
      kind: 'ingest',
      kb_id: 0,
      theme_label: '기본 지식베이스',
      title: 'Misalignment Reports and Notices',
      stage: isExtract ? 'extract' : 'resolve',
      stages: ['fetch', 'extract', 'resolve', 'detail', 'done'],
      msg: isExtract ? '엔티티 및 관계 구조화 추출 중…' : '엔티티 대조 텐서 계산 중 (75%)',
      progress: isExtract ? 45 : 75,
      started_at: Date.now() / 1000 - 75,
      heatmap_matrix: isExtract ? null : {
        rows: ['vCenter Server', 'ESXi 8.0'],
        cols: ['vCenter', 'ESXi Host'],
        matrix: [[0.95, 0.45], [0.3, 0.98]],
        threshold_auto_merge: 0.93,
        threshold_borderline: 0.72
      }
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        documents: 5,
        entities: 42,
        relations: 68,
        kb_id: 0,
        theme_label: '기본 지식베이스',
        ingesting: true,
        active_jobs: [jobPayload],
        active_ingest: jobPayload
      })
    });
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'matrix');

  // Obsolete multi-slot containers must not exist
  await expect(page.locator('#job-slots-container')).toHaveCount(0);

  // Verify Unified Cockpit Header & Badges
  await expect(page.locator('#cockpit-status-text')).toHaveText('적재 진행 중');
  await expect(page.locator('#cockpit-theme-badge')).toContainText('기본 지식베이스');
  await expect(page.locator('#cockpit-kind-badge')).toHaveText('적재 파이프라인');
  await expect(page.locator('#cockpit-big-pct')).toHaveText('45%');
  await expect(page.locator('#cockpit-elapsed-time')).toContainText('경과');
  await expect(page.locator('#matrix-target-doc')).toContainText('Misalignment Reports and Notices');

  // Verify Desktop 5-Stage Stepper is visible and Mobile Stepper is hidden
  const desktopStepper = page.locator('#cockpit-desktop-stepper');
  const mobileStepper = page.locator('#cockpit-mobile-stepper');
  await expect(desktopStepper).toBeVisible();
  await expect(mobileStepper).toBeHidden();

  const stepCards = desktopStepper.locator('.cockpit-step-card');
  await expect(stepCards).toHaveCount(5);
  await expect(stepCards.nth(0)).toHaveClass(/completed/);
  await expect(stepCards.nth(0)).toContainText('1. 수집 (Fetch)');
  await expect(stepCards.nth(1)).toHaveClass(/current/);
  await expect(stepCards.nth(1)).toContainText('2. 추출 (Extract)');
  await expect(stepCards.nth(2)).toHaveClass(/pending/);
  await expect(stepCards.nth(2)).toContainText('3. 대조 (Resolve)');

  // Verify Live Stream Extraction Card is shown during pre-resolve stage (not just a bare spinner)
  const liveCard = page.locator('.cockpit-live-stream-card');
  await expect(liveCard).toBeVisible();
  await expect(liveCard.locator('.wave-msg')).toContainText('엔티티 및 관계 구조화 추출 중');
  await expect(liveCard.locator('.cockpit-live-log-line.done')).toHaveCount(1);
  await expect(liveCard.locator('.cockpit-live-log-line.active')).toContainText('엔티티 및 관계 구조화 추출 중');

  // Verify Tablet viewport (820x1180) also preserves the 5-stage milestone stepper
  await page.setViewportSize({ width: 820, height: 1180 });
  await expect(desktopStepper).toBeVisible();
  await expect(mobileStepper).toBeHidden();

  // Transition to Resolve stage and trigger poll update
  await page.setViewportSize({ width: 1280, height: 800 });
  currentPhase = 'resolve';
  await page.evaluate(() => window.claireDebug.pollForUpdates());

  await expect(page.locator('#cockpit-big-pct')).toHaveText('75%');
  await expect(stepCards.nth(1)).toHaveClass(/completed/);
  await expect(stepCards.nth(2)).toHaveClass(/current/);
  await expect(page.locator('#matrix-tbody tr')).toHaveCount(2);

  expect(pageErrors).toEqual([]);
});

test('Unified Ingest Cockpit renders compact 1-line progress gauge on mobile without vertical accordion bloat', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });

  await page.route('**/stats', async (route) => {
    const jobPayload = {
      job_id: 'j_mobile_1',
      active: true,
      kind: 'reingest',
      kb_id: 0,
      theme_label: '기본 지식베이스',
      title: 'vSphere 8.0 Release Notes',
      stage: 'resolve',
      stages: ['fetch', 'extract', 'resolve', 'detail', 'done'],
      msg: '엔티티 대조 텐서 계산 중',
      progress: 75,
      started_at: Date.now() / 1000 - 95,
      heatmap_matrix: {
        rows: ['vCenter Server', 'ESXi 8.0'],
        cols: ['vCenter', 'ESXi Host'],
        matrix: [[0.95, 0.45], [0.3, 0.98]],
        threshold_auto_merge: 0.93,
        threshold_borderline: 0.72
      }
    };
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        documents: 5,
        entities: 42,
        relations: 68,
        kb_id: 0,
        theme_label: '기본 지식베이스',
        ingesting: true,
        active_jobs: [jobPayload],
        active_ingest: jobPayload
      })
    });
  });

  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'matrix');

  // Desktop 5-stage grid hidden on mobile; compact 1-line mobile stepper visible
  const desktopStepper = page.locator('#cockpit-desktop-stepper');
  const mobileStepper = page.locator('#cockpit-mobile-stepper');
  await expect(desktopStepper).toBeHidden();
  await expect(mobileStepper).toBeVisible();

  await expect(page.locator('#cockpit-mobile-stage')).toHaveText('단계 [3/5]: 3. 대조 (Resolve)');
  await expect(page.locator('#cockpit-mobile-pct')).toHaveText('75%');
  await expect(page.locator('#cockpit-kind-badge')).toHaveText('재적재 파이프라인');
  await expect(page.locator('#matrix-tbody tr')).toHaveCount(2);

  expect(pageErrors).toEqual([]);
});

test('active ingest during boot correctly mounts matrix view and recovers on brand click', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });

  let ingestActive = true;
  await page.route('**/stats', async (route) => {
    if (ingestActive) {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          documents: 5,
          entities: 42,
          relations: 68,
          kb_id: 0,
          theme_label: '기본 지식베이스',
          ingesting: true,
          active_ingest: {
            active: true,
            job_id: 'j_boot',
            kind: 'ingest',
            kb_id: 0,
            theme_label: '기본 지식베이스',
            title: '부트 적재 테스트',
            stage: 'extract',
            stages: ['fetch', 'extract', 'resolve', 'detail', 'done'],
            msg: '엔티티 추출 진행 중',
            progress: 45,
            heatmap_matrix: null
          }
        })
      });
    } else {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          documents: 6,
          entities: 50,
          relations: 72,
          kb_id: 0,
          theme_label: '기본 지식베이스',
          ingesting: false,
          active_jobs: [],
          active_ingest: null
        })
      });
    }
  });

  // 1. Initial boot directly mounts matrix view
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'matrix');
  await expect(page.locator('#matrix-topbar')).toBeVisible();
  await expect(page.locator('#matrix-target-doc')).toContainText('부트 적재 테스트');
  await expect(page.locator('#matrix-stepper')).toBeVisible();

  // 2. Job finishes, user clicks brand logo
  ingestActive = false;
  const brand = page.locator('a.brand');
  await expect(brand).toBeVisible();
  await brand.click();

  // 3. Recovers to home view & title
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page).toHaveTitle('Claire Bible — 홈');
  await expect(page.locator('#homewrap')).toBeVisible();

  expect(pageErrors).toEqual([]);
});


test.describe('document list with server pages', () => {
  // The service worker must not answer GET /documents, because the tests count, delay, and abort these requests.
  test.use({ serviceWorkers: 'block' });

  const docRequests = page => {
    const urls = [];
    page.on('request', request => { const u = new URL(request.url()); if (u.pathname.endsWith('/documents')) urls.push(u); });
    return urls;
  };

  test('list pane is named 목록, shows 31 documents first, and gets the next pages from the server on scroll', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const requests = docRequests(page);
    await page.setViewportSize({ width: 1400, height: 500 });
    await waitForClaire(page);

    await expect(page.locator('#mobile-doclist-btn')).toHaveAttribute('aria-label', '목록');
    // The home view defaults to the document list, and the graph view defaults to the entity list (unit LP-3).
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
    await page.locator('#centertab-graph').click();
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'entities');
    await page.locator('#centertab-reader').click();
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
    await expect(page.locator('#entlist')).toBeHidden();

    // The first page has 31 documents. The title shows the total of the server (68 visible documents in the seed).
    const items = page.locator('#doclist .docitem');
    await expect(items).toHaveCount(31);
    await expect(page.locator('#doclist .docskel')).toHaveCount(0);
    await expect(page.locator('#listtitle')).toHaveText('문서 31 / 68');
    await expect(page.locator('#listtopbtn')).toBeHidden();
    const pageRequests = () => requests.filter(u => u.searchParams.get('pinned') === '0');
    expect(pageRequests().length).toBe(1);
    expect(pageRequests()[0].searchParams.get('limit')).toBe('31');
    expect(pageRequests()[0].searchParams.get('hidden')).toBe('0');
    expect(pageRequests()[0].searchParams.has('before')).toBe(false);
    await expect(items.nth(0)).toContainText('테스트 문서 2');
    await expect(items.nth(30)).toContainText('페이징 자료 28');

    // Scroll to the end: the list requests the second page from the server (before=<fetched_at>:<id> of the last row).
    await page.locator('#doclist').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(items).toHaveCount(62);
    await expect(page.locator('#listtitle')).toHaveText('문서 62 / 68');
    expect(pageRequests().length).toBe(2);
    expect(pageRequests()[1].search).toContain('before=');
    expect(pageRequests()[1].searchParams.get('before')).toMatch(/^\d+(\.\d+)?:doc-page-28$/);
    expect(pageRequests()[1].searchParams.get('limit')).toBe('31');
    await expect(page.locator('#doclist .list-end')).toHaveCount(0);
    await expect(items.nth(31)).toContainText('페이징 자료 29');

    // Scroll again: the third page is the last one, and the end mark shows.
    await page.locator('#doclist').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(items).toHaveCount(68);
    await expect(page.locator('#doclist .list-end')).toHaveText('— 끝 (68) —');
    await expect(page.locator('#listtitle')).toHaveText('문서 68 / 68');
    expect(pageRequests().length).toBe(3);
    expect(pageRequests()[2].searchParams.get('before')).toMatch(/:doc-old-19$/);
    await expect(items.nth(67)).toContainText('오래된 자료 25');
    // No document shows two times, and each date header shows one time across the pages.
    const titles = await page.locator('#doclist .docitem b').allTextContents();
    expect(new Set(titles).size).toBe(68);
    const days = await page.locator('#doclist .dday').allTextContents();
    expect(days.length).toBeGreaterThanOrEqual(3);
    expect(new Set(days).size).toBe(days.length);

    // A render with the same filter keeps the rendered count.
    await page.evaluate(() => renderDocs(''));
    await expect(items).toHaveCount(68);

    // A background refresh gets only the first page again (no before parameter). The loaded pages, the rendered count, and the scroll position stay.
    await page.locator('#doclist').evaluate(el => { el.scrollTop = el.scrollHeight; });
    const topBefore = await page.locator('#doclist').evaluate(el => el.scrollTop);
    expect(topBefore).toBeGreaterThan(500);
    const before = requests.length;
    await page.evaluate(() => refreshDocs());
    expect(requests.length).toBe(before + 3);
    expect(requests.slice(before).filter(u => u.searchParams.has('before')).length).toBe(0);
    expect(requests.slice(before).map(u => u.searchParams.get('pinned') + '/' + u.searchParams.get('hidden')).sort()).toEqual(['0/0', '1/null', 'null/1']);
    await expect(items).toHaveCount(68);
    await expect(page.locator('#listtitle')).toHaveText('문서 68 / 68');
    expect(await page.locator('#doclist').evaluate(el => el.scrollTop)).toBe(topBefore);
    expect(await page.locator('#doclist .docitem b').allTextContents()).toEqual(titles);

    await expect(page.locator('#listtopbtn')).toBeVisible();
    await page.locator('#listtopbtn').click();
    await expect.poll(() => page.locator('#doclist').evaluate(el => el.scrollTop)).toBe(0);
    await expect(page.locator('#listtopbtn')).toBeHidden();

    // The stream view keeps the document list and its rendered rows.
    await page.evaluate(() => setCenterView('stream'));
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
    await expect(page.locator('#doclist')).toBeVisible();
    await expect(items).toHaveCount(68);
    expect(pageErrors).toEqual([]);
  });

  test('a background refresh after a partial scroll keeps the second page and adds no request for it', async ({ page }) => {
    const requests = docRequests(page);
    await page.setViewportSize({ width: 1400, height: 500 });
    await waitForClaire(page);
    await page.locator('#centertab-reader').click();
    const items = page.locator('#doclist .docitem');
    await expect(items).toHaveCount(31);
    await page.locator('#doclist').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(items).toHaveCount(62);
    const before = requests.length;
    await page.evaluate(() => refreshDocs());
    await expect(items).toHaveCount(62);
    await expect(page.locator('#listtitle')).toHaveText('문서 62 / 68');
    expect(requests.slice(before).filter(u => u.searchParams.has('before')).length).toBe(0);
    // The list continues from the cursor of the second page.
    await page.locator('#doclist').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(items).toHaveCount(68);
    expect(requests[requests.length - 1].searchParams.get('before')).toMatch(/:doc-old-19$/);
    expect(new Set(await page.locator('#doclist .docitem b').allTextContents()).size).toBe(68);
  });

  test('a deep link to a document after the first page loads the pages up to it and shows its row', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const requests = docRequests(page);
    await page.setViewportSize({ width: 1400, height: 500 });
    // A member opens the old link of a document: 308 to its permalink, and the permalink gives the workspace with the document open (PERMALINK_UNIFICATION_DESIGN.md U5, Section 6 rule 4). A caller that is not a member gets the item view.
    await page.goto('/?t=e2e-deeplink-token-0123456789abcdef', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    requests.length = 0;
    await page.goto('/?doc=doc-page-35', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/silvercord\/[2-9a-z]{16}$/);
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    await expect(page.locator('#reader')).toContainText('페이징 자료 35');
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
    // The document is row 38 of the list (3 + 35), thus the rendered window has two pages.
    const active = page.locator('#doclist .docitem.active');
    await expect(active).toHaveCount(1);
    await expect(active).toContainText('페이징 자료 35');
    await expect(active).toBeInViewport();
    await expect(page.locator('#doclist .docitem')).toHaveCount(62);
    await expect(page.locator('#listtitle')).toHaveText('문서 62 / 68');
    expect(requests.filter(u => u.searchParams.get('id') === 'doc-page-35').length).toBe(1);
    expect(requests.filter(u => u.searchParams.has('before')).length).toBe(1);
    // The graph view picker shows the title of the open document.
    await expect(page.locator('#graphdoclabel')).toHaveText('페이징 자료 35');
    expect(pageErrors).toEqual([]);
  });

  test('a failed page request shows a retry control and does not repeat, and the retry loads the page', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1400, height: 500 });
    await waitForClaire(page);
    await page.locator('#centertab-reader').click();
    const items = page.locator('#doclist .docitem');
    await expect(items).toHaveCount(31);
    let fail = true, pageCalls = 0;
    await page.route(url => url.pathname.endsWith('/documents') && url.searchParams.has('before'), async route => {
      pageCalls += 1;
      if (fail) { await route.abort(); return; }
      await route.continue();
    });
    await page.locator('#doclist').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(page.locator('#doclist .list-more-error')).toContainText('다음 문서를 불러오지 못했습니다.');
    await expect(page.locator('#doclist .list-sentinel')).toHaveCount(0);
    await page.waitForTimeout(800);
    expect(pageCalls).toBe(1);
    await expect(items).toHaveCount(31);
    await expect(page.locator('#listtitle')).toHaveText('문서 31 / 68');
    fail = false;
    await page.locator('#doclist .list-retry').click();
    await expect(items).toHaveCount(62);
    await expect(page.locator('#doclist .list-more-error')).toHaveCount(0);
    expect(pageCalls).toBe(2);
    expect(pageErrors).toEqual([]);
  });

  test('filter text gets its pages from the server, a late response for an old filter is ignored, and the picker finds a document after the loaded pages', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const requests = docRequests(page);
    await page.setViewportSize({ width: 1400, height: 500 });
    await waitForClaire(page);
    // A pinned document that is older than the loaded pages shows in the pinned list (the seed has no pinned document, thus the response of the pinned request is made here).
    const old25 = (await (await page.request.get('/documents?id=doc-old-25')).json()).documents[0];
    expect(old25.title).toBe('오래된 자료 25');
    await page.route(url => url.pathname.endsWith('/documents') && url.searchParams.get('pinned') === '1', route => route.fulfill({ json: { documents: [{ ...old25, pinned: 1 }], next_before: null, total: 1 } }));
    await page.route(url => url.pathname.endsWith('/documents') && url.searchParams.get('q') === '페이징', async route => { await new Promise(resolve => setTimeout(resolve, 900)); await route.continue(); });
    await page.locator('#centertab-reader').click();
    const items = page.locator('#doclist .docitem');
    await expect(items).toHaveCount(31);
    await page.evaluate(() => refreshDocs());
    await expect(page.locator('#pinnedhead')).toHaveText('즐겨찾기 (1)');
    await expect(page.locator('#pinnedlist .docitem')).toContainText(['오래된 자료 25']);

    // Two filter texts with no pause between them: the request goes when the typing stops, thus only the second filter text is sent. The list shows the second one only.
    await page.evaluate(() => { onCenterSearchInput('페이징'); onCenterSearchInput('오래된 자료 0'); });
    await expect(items).toHaveCount(9);
    await expect(page.locator('#listtitle')).toHaveText('문서 9 / 9');
    await expect(page.locator('#stat')).toHaveText('9개 발견');
    await page.waitForTimeout(1300);
    await expect(items).toHaveCount(9);
    expect(await items.locator('b').allTextContents()).toEqual(['오래된 자료 01', '오래된 자료 02', '오래된 자료 03', '오래된 자료 04', '오래된 자료 05', '오래된 자료 06', '오래된 자료 07', '오래된 자료 08', '오래된 자료 09']);
    expect(requests.some(u => u.searchParams.get('q') === '오래된 자료 0' && u.searchParams.get('limit') === '31')).toBe(true);
    expect(requests.some(u => u.searchParams.get('q') === '페이징')).toBe(false);

    // A filter with more than one page: 40 documents match, and the second page comes from the server with the same filter.
    await page.evaluate(() => onCenterSearchInput('페이징'));
    await expect(items).toHaveCount(31);
    await expect(page.locator('#listtitle')).toHaveText('문서 31 / 40');
    await page.locator('#doclist').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(items).toHaveCount(40);
    await expect(page.locator('#doclist .list-end')).toHaveText('— 끝 (40) —');
    expect(requests.some(u => u.searchParams.get('q') === '페이징' && u.searchParams.has('before'))).toBe(true);

    // The search is closed: the list goes back to the loaded pages with no new page request.
    const count = requests.filter(u => u.searchParams.get('pinned') === '0').length;
    await page.evaluate(() => clearServerSearch());
    await expect(items).toHaveCount(31);
    await expect(page.locator('#listtitle')).toHaveText('문서 31 / 68');
    expect(requests.filter(u => u.searchParams.get('pinned') === '0').length).toBe(count);

    // The graph view picker: a document after the loaded pages is found by its title.
    await page.locator('#centertab-graph').click();
    // The picker button shows only on a small screen, thus the test opens the picker and selects the option with script calls.
    await page.evaluate(() => openGraphDocPicker());
    await expect(page.locator('#graphdocmore')).toHaveCount(1);
    await expect(page.locator('#graphdoclist .graphdocoption', { hasText: '오래된 자료 17' })).toHaveCount(0);
    await page.evaluate(() => { const input = document.getElementById('graphdocq'); input.value = '오래된 자료 17'; input.dispatchEvent(new Event('input', { bubbles: true })); });
    await expect(page.locator('#graphdoclist .graphdocoption', { hasText: '오래된 자료 17' })).toHaveCount(1);
    expect(requests.some(u => u.searchParams.get('q') === '오래된 자료 17' && !u.searchParams.has('pinned'))).toBe(true);
    await page.locator('#graphdoclist .graphdocoption', { hasText: '오래된 자료 17' }).evaluate(el => el.click());
    await expect(page.locator('#graphdoclabel')).toHaveText('오래된 자료 17');
    expect(await page.evaluate(() => activeDoc)).toBe('doc-old-17');
    expect(pageErrors).toEqual([]);
  });
});


test('graph view shows the entity list in degree order with paging, one open row, filters, and keyboard control', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const nodeRequests = [];
  page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/node')) nodeRequests.push(request.url()); });
  await page.setViewportSize({ width: 1400, height: 500 });
  await waitForClaire(page);
  await page.locator('#centertab-graph').click();

  // The graph view shows the entity list. The document list is hidden.
  await expect(page.locator('#docs')).toHaveAttribute('data-list', 'entities');
  await expect(page.locator('#docs')).toHaveAttribute('aria-label', '엔티티 목록');
  await expect(page.locator('#entlist')).toBeVisible();
  await expect(page.locator('#doclist')).toBeHidden();
  // The graph status shows in the entity list only. The document list hides it, also when the graph writes it again.
  await expect(page.locator('#stat .graphstat')).toBeVisible();
  await page.locator('#centertab-reader').click();
  await page.evaluate(() => applyView());
  await expect(page.locator('#stat .graphstat')).toHaveCount(1);
  await expect(page.locator('#stat .graphstat')).toBeHidden();
  await page.locator('#centertab-graph').click();
  await expect(page.locator('#stat .graphstat')).toBeVisible();

  // 31 rows first, in degree order (then label order).
  const rows = page.locator('#entlist .entitem');
  await expect(rows).toHaveCount(31);
  await expect(page.locator('#listtitle')).toHaveText('엔티티 31 / 44');
  await expect(rows.nth(0).locator('.entname')).toHaveText('허브 엔티티');
  await expect(rows.nth(0).locator('.entdeg')).toHaveText('40');
  await expect(rows.nth(0).locator('.entdeg')).toHaveAttribute('title', '관계 40개');
  await expect(rows.nth(0).locator('.entbar i')).toHaveAttribute('style', 'width:100%');
  await expect(rows.nth(1).locator('.entname')).toHaveText('목록 엔티티 01');
  await expect(rows.nth(1).locator('.entdeg')).toHaveText('8');
  await expect(rows.nth(1).locator('.entbar i')).toHaveAttribute('style', 'width:20%');
  await expect(rows.nth(2).locator('.entname')).toHaveText('목록 엔티티 02');
  const degrees = await page.locator('#entlist .entdeg').allTextContents();
  expect(degrees.map(Number)).toEqual([...degrees.map(Number)].sort((a, b) => b - a));
  const dotColor = await rows.nth(0).locator('.entdot').evaluate(el => el.style.background);
  expect(dotColor).toBe(await page.evaluate(() => { const i = document.createElement('i'); i.style.background = TYPE_COLORS.Framework; return i.style.background; }));

  // A scroll to the end adds the remaining rows.
  await page.locator('#entlist').evaluate(el => { el.scrollTop = el.scrollHeight; });
  await expect(rows).toHaveCount(44);
  await expect(page.locator('#entlist .list-end')).toHaveText('— 끝 (44) —');
  await expect(page.locator('#listtitle')).toHaveText('엔티티 44 / 44');
  await page.locator('#entlist').evaluate(el => { el.scrollTop = el.scrollHeight; });
  await expect(page.locator('#listtopbtn')).toBeVisible();
  await page.locator('#listtopbtn').click();
  await expect.poll(() => page.locator('#entlist').evaluate(el => el.scrollTop)).toBe(0);

  // Pointer on a row: the graph shows the hover state of the node and the camera does not move.
  const viewBefore = await page.evaluate(() => ({ ...window.claireDebug.viewpos, scale: window.claireDebug.scale }));
  await rows.nth(1).locator('.entrow').hover();
  await expect.poll(() => page.evaluate(() => net.hoverId)).toBe('ent-list-01');
  // The idle graph has a small camera drift. A camera move to a node is much larger and changes the scale.
  const viewAfter = await page.evaluate(() => ({ ...window.claireDebug.viewpos, scale: window.claireDebug.scale }));
  expect(Math.hypot(viewAfter.x - viewBefore.x, viewAfter.y - viewBefore.y)).toBeLessThan(2);
  expect(viewAfter.scale).toBeCloseTo(viewBefore.scale, 2);
  expect(await page.evaluate(() => window.claireDebug.selected)).toBeNull();

  // A click opens the row with one GET /node request and selects the node in the graph.
  const hub = rows.nth(0);
  await hub.locator('.entrow').click();
  await expect(hub.locator('.entrow')).toHaveAttribute('aria-expanded', 'true');
  await expect(hub.locator('.entrow')).toHaveAttribute('aria-controls', 'entbody');
  const body = page.locator('#entbody');
  await expect(body).toHaveAttribute('role', 'region');
  await expect(body.locator('h4').nth(0)).toHaveText('관찰/주장');
  await expect(body.locator('.entobs li')).toHaveCount(5);
  await expect(body.locator('h4').nth(1)).toHaveText('관계 (40)');
  expect(nodeRequests.length).toBe(1);
  expect(await page.evaluate(() => window.claireDebug.selected)).toBe('ent-hub');
  expect(await page.evaluate(() => window.claireDebug.sel)).toEqual(['ent-hub']);
  // The detail panel has no 관찰/주장 section and no 연결 section (unit LP-4). Its button shows the number of the two (7 + 40).
  await expect(page.locator('#panel h2')).toContainText('허브 엔티티');
  await expect(page.locator('#panel h3')).toHaveCount(0);
  await expect(page.locator('#panel #node-listrow-btn')).toHaveText('관찰/주장 · 관계 보기 (47)');

  await body.locator('.entmore').click();
  await expect(body.locator('.entmore')).toHaveCount(0);
  await expect(body.locator('.entobs li')).toHaveCount(7);

  // 연결 is grouped by relation type. A group with more than 10 items is closed.
  const group = body.locator('details.entrel');
  await expect(group).toHaveCount(1);
  await expect(group.locator('summary')).toHaveText('uses 40');
  expect(await group.evaluate(el => el.open)).toBe(false);
  await group.locator('summary').click();

  // A click on a neighbor after the rendered window: the list grows, opens that row, and scrolls to it.
  await group.locator('a', { hasText: '목록 엔티티 40' }).click();
  const opened = page.locator('#entlist .entitem.open');
  await expect(opened).toHaveCount(1);
  await expect(opened.locator('.entname')).toHaveText('목록 엔티티 40');
  await expect(opened.locator('#entbody h4').nth(1)).toHaveText('관계 (1)');
  expect(await opened.locator('details.entrel').evaluate(el => el.open)).toBe(true);
  await expect(opened.locator('.entrow')).toBeInViewport();
  await expect(opened.locator('.entrow')).toBeFocused();
  await expect(hub.locator('.entrow')).toHaveAttribute('aria-expanded', 'false');
  expect(nodeRequests.length).toBe(2);
  expect(await page.evaluate(() => window.claireDebug.selected)).toBe('ent-list-40');

  // A second click closes the row and sends no request.
  await opened.locator('.entrow').click();
  await expect(page.locator('#entlist .entitem.open')).toHaveCount(0);
  expect(nodeRequests.length).toBe(2);

  // The degree threshold filters the list the same as the graph. The selected entity keeps a row with the mark 필터 밖.
  await expect(page.locator('#entchips')).toBeHidden();
  await page.evaluate(() => setDeg(2));
  await expect(rows).toHaveCount(10);
  await expect(rows.nth(0)).toContainText('목록 엔티티 40');
  await expect(rows.nth(0).locator('.entout')).toHaveText('필터 밖');
  await expect(rows.nth(1).locator('.entname')).toHaveText('허브 엔티티');
  await expect(page.locator('#entchips .entchip')).toHaveText(['관계 ≥ 2']);
  // The title counts the matching entities only, not the row with the mark 필터 밖.
  await expect(page.locator('#listtitle')).toHaveText('엔티티 9 / 9');
  const listed = await page.evaluate(() => { const ids = []; allNodes.forEach(n => { if (isNodeListed(n)) ids.push(n.id); }); return ids.sort(); });
  const shown = await page.evaluate(() => window.claireDebug.visibleNodePoints().map(item => item.id).sort());
  expect(listed).toEqual(shown);
  expect(listed.length).toBe(9);
  await page.locator('#entchips .entchip-reset').click();
  await expect(page.locator('#entchips')).toBeHidden();
  await expect(rows).toHaveCount(31);
  expect(await page.evaluate(() => window.claireDebug.curMinDeg)).toBe(0);
  // The reset button is removed. The focus goes to the sort control.
  await expect(page.locator('.entsort-btn[data-sort="degree"]')).toBeFocused();

  // Search text filters the list.
  // The alias test, which runs before this test, changes the name of 엔티티 A. Thus the search uses 엔티티 C.
  await page.evaluate(() => hl('엔티티 C'));
  await expect(rows.locator('.entname')).toHaveText(['목록 엔티티 40', '엔티티 C']);
  await expect(rows.nth(0).locator('.entout')).toHaveText('필터 밖');
  await expect(page.locator('#entchips .entchip')).toHaveText(['검색 1개']);
  await page.locator('#entchips .entchip-reset').click();
  await expect(rows).toHaveCount(31);

  // Sort by name.
  await page.locator('.entsort-btn[data-sort="name"]').click();
  await expect(page.locator('.entsort-btn[data-sort="name"]')).toHaveAttribute('aria-pressed', 'true');
  await expect(rows.nth(0).locator('.entname')).toHaveText('목록 엔티티 01');
  await page.locator('.entsort-btn[data-sort="degree"]').click();
  await expect(rows.nth(0).locator('.entname')).toHaveText('허브 엔티티');

  // Keyboard: the arrow keys move the focus, Enter opens a row, and Escape closes it.
  await rows.nth(0).locator('.entrow').focus();
  await page.keyboard.press('ArrowDown');
  await expect(rows.nth(1).locator('.entrow')).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(rows.nth(1).locator('.entrow')).toHaveAttribute('aria-expanded', 'true');
  await expect(rows.nth(1).locator('#entbody h4').nth(1)).toHaveText('관계 (8)');
  await expect(rows.nth(1).locator('.entrow')).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.locator('#entlist .entitem.open')).toHaveCount(0);
  await expect(rows.nth(1).locator('.entrow')).toBeFocused();
  await page.keyboard.press('ArrowUp');
  await expect(rows.nth(0).locator('.entrow')).toBeFocused();

  // Selection on the canvas opens the row of that node.
  await page.evaluate(() => { clearSelections(); resetGraphCamera(); });
  await page.waitForTimeout(900);
  const point = await page.evaluate(() => {
    const box = document.getElementById('net').getBoundingClientRect();
    return window.claireDebug.visibleNodePoints().find(item => item.x > 40 && item.y > 40 && item.x < box.width - 40 && item.y < box.height - 40) || null;
  });
  expect(point).not.toBeNull();
  await page.locator('#net').click({ position: { x: Math.round(point.x), y: Math.round(point.y) }, force: true });
  await expect.poll(() => page.evaluate(() => window.claireDebug.selected)).not.toBeNull();
  const canvasId = await page.evaluate(() => window.claireDebug.selected);
  await expect(page.locator('#entlist .entitem.open')).toHaveCount(1);
  await expect(page.locator('#entlist .entitem.open')).toHaveAttribute('data-ent-id', canvasId);
  await expect(page.locator('#entlist .entitem.open .entrow')).toBeInViewport();

  // The reader view shows the document list.
  await page.locator('#centertab-reader').click();
  await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
  await expect(page.locator('#doclist')).toBeVisible();
  await expect(page.locator('#entlist')).toBeHidden();
  await expect(page.locator('#enttools')).toBeHidden();
  await expect(page.locator('#listtitle')).toContainText('문서');
  expect(pageErrors).toEqual([]);
});

test('mobile entity row keeps the list drawer open and its button shows the node in the graph', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 800 });
  await waitForClaire(page);
  // The graph is stable on the desktop size. Then the page changes to the mobile size.
  await page.setViewportSize({ width: 390, height: 800 });
  await page.evaluate(() => { setCenterView('graph'); openDocDrawer(); });
  await expect(page.locator('body')).toHaveClass(/docdrawer-open/);
  await expect(page.locator('#entlist')).toBeVisible();

  const hub = page.locator('#entlist .entitem').first();
  await hub.locator('.entrow').click();
  await expect(hub.locator('.entrow')).toHaveAttribute('aria-expanded', 'true');
  await expect(hub.locator('#entbody h4').first()).toHaveText('관찰/주장');
  await expect(page.locator('body')).toHaveClass(/docdrawer-open/);
  expect(await page.evaluate(() => window.claireDebug.selected)).toBe('ent-hub');

  const go = hub.locator('.entgo');
  await expect(go).toHaveText('⌖ 그래프에서 보기');
  await go.click();
  await expect(page.locator('body')).not.toHaveClass(/docdrawer-open/);
  expect(await page.evaluate(() => window.claireDebug.selected)).toBe('ent-hub');
  expect(pageErrors).toEqual([]);
});


test.describe('node detail panel and node request order', () => {
  // The service worker must not answer GET /node, because the tests delay and abort these requests with page.route().
  test.use({ serviceWorkers: 'block' });

  test('a late GET /node response does not replace the newer node, a failed request shows an error, and the open row keeps its state', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1400, height: 500 });
    await waitForClaire(page);
    await page.locator('#centertab-graph').click();
    let mode = 'delay-hub';
    await page.route(url => url.pathname.endsWith('/node'), async route => {
      const id = new URL(route.request().url()).searchParams.get('id');
      if (mode === 'delay-hub' && id === 'ent-hub') await new Promise(resolve => setTimeout(resolve, 900));
      if (mode === 'fail' && id === 'ent-list-02') { await route.abort(); return; }
      await route.continue();
    });

    // Click the hub (slow response), then the next row (fast response). The late response for the hub is ignored.
    const rows = page.locator('#entlist .entitem');
    await rows.nth(0).locator('.entrow').click();
    await rows.nth(1).locator('.entrow').click();
    await expect(page.locator('#panel h2')).toContainText('목록 엔티티 01');
    await expect(page.locator('#entlist .entitem.open #entbody h4').nth(1)).toHaveText('관계 (8)');
    await page.waitForTimeout(1300);
    await expect(page.locator('#panel h2')).toContainText('목록 엔티티 01');
    expect(await page.evaluate(() => currentNodeData.id)).toBe('ent-list-01');
    await expect(page.locator('#entlist .entitem.open')).toHaveAttribute('data-ent-id', 'ent-list-01');
    await expect(page.locator('#entlist .entitem.open #entbody h4').nth(1)).toHaveText('관계 (8)');
    // The open row has the active mark.
    await expect(page.locator('#entlist .entitem.open')).toHaveClass(/active/);

    // A failed request: the row shows an error text, not the loading text.
    mode = 'fail';
    await rows.nth(2).locator('.entrow').click();
    await expect(page.locator('#entlist .entitem.open')).toHaveAttribute('data-ent-id', 'ent-list-02');
    await expect(page.locator('#entbody')).toContainText('불러오지 못했습니다');
    await expect(page.locator('#entbody')).not.toContainText('로딩');
    // The failed request removes the data of the earlier node.
    expect(await page.evaluate(() => currentNodeData)).toBeNull();
    await expect(page.locator('#panel')).toContainText('엔티티 정보를 불러오지 못했습니다.');

    // A click on the row in the error state sends the request again. The row does not close.
    mode = 'pass';
    await rows.nth(2).locator('.entrow').click();
    await expect(page.locator('#entlist .entitem.open')).toHaveAttribute('data-ent-id', 'ent-list-02');
    await expect(page.locator('#entbody h4').nth(1)).toHaveText('관계 (2)');
    await expect(page.locator('#panel h2')).toContainText('목록 엔티티 02');

    // The open row keeps the 더 보기 state and the state of a 연결 group after a new render.
    await rows.nth(0).locator('.entrow').click();
    const body = page.locator('#entbody');
    await body.locator('.entmore').click();
    await expect(body.locator('.entobs li')).toHaveCount(7);
    await body.locator('details.entrel summary').click();
    expect(await body.locator('details.entrel').evaluate(el => el.open)).toBe(true);
    await page.evaluate(() => loadNode('ent-hub'));
    await page.waitForTimeout(400);
    await expect(body.locator('.entobs li')).toHaveCount(7);
    expect(await body.locator('details.entrel').evaluate(el => el.open)).toBe(true);

    // The state of a 연결 group stays when a render comes in the same task as the click, before the toggle event of the details element.
    // Sequence 1: 더 보기 first, then open a group that is closed by default, then a filter change.
    await rows.nth(0).locator('.entrow').click();
    await expect(page.locator('#entlist .entitem.open')).toHaveCount(0);
    await rows.nth(0).locator('.entrow').click();
    await expect(body.locator('h4').nth(1)).toHaveText('관계 (40)');
    expect(await body.locator('details.entrel').evaluate(el => el.open)).toBe(false);
    await body.locator('.entmore').click();
    await page.evaluate(() => { document.querySelector('#entbody details.entrel[data-rel="uses"] > summary').click(); setDeg(1); });
    await expect(page.locator('#entchips .entchip')).toHaveText(['관계 ≥ 1']);
    await expect(body.locator('.entobs li')).toHaveCount(7);
    expect(await body.locator('details.entrel[data-rel="uses"]').evaluate(el => el.open)).toBe(true);
    await page.evaluate(() => setDeg(0));
    await page.waitForTimeout(100);
    expect(await body.locator('details.entrel[data-rel="uses"]').evaluate(el => el.open)).toBe(true);
    // Sequence 2: close a group that is open by default, then a filter change.
    await rows.nth(1).locator('.entrow').click();
    await expect(body.locator('h4').nth(1)).toHaveText('관계 (8)');
    expect(await body.locator('details.entrel[data-rel="relates_to"]').evaluate(el => el.open)).toBe(true);
    await page.evaluate(() => { document.querySelector('#entbody details.entrel[data-rel="relates_to"] > summary').click(); setDeg(1); });
    await expect(page.locator('#entchips .entchip')).toHaveText(['관계 ≥ 1']);
    expect(await body.locator('details.entrel[data-rel="relates_to"]').evaluate(el => el.open)).toBe(false);
    expect(await body.locator('details.entrel[data-rel="uses"]').evaluate(el => el.open)).toBe(true);
    await page.evaluate(() => setDeg(0));
    await page.waitForTimeout(100);
    expect(await body.locator('details.entrel[data-rel="relates_to"]').evaluate(el => el.open)).toBe(false);
    // A real click on the summary also writes the state.
    await body.locator('details.entrel[data-rel="relates_to"] summary').click();
    await page.evaluate(() => setDeg(1));
    expect(await body.locator('details.entrel[data-rel="relates_to"]').evaluate(el => el.open)).toBe(true);
    await page.evaluate(() => setDeg(0));

    // After a sort change, an open row that is after the first 31 rows stays in the list. The list does not scroll to it.
    await page.evaluate(() => selectEntity('ent-list-40'));
    await expect(page.locator('#entlist .entitem.open')).toHaveAttribute('data-ent-id', 'ent-list-40');
    await page.locator('.entsort-btn[data-sort="name"]').click();
    await expect(rows.nth(0).locator('.entname')).toHaveText('목록 엔티티 01');
    await expect(page.locator('#entlist .entitem.open')).toHaveAttribute('data-ent-id', 'ent-list-40');
    expect(await rows.count()).toBeGreaterThan(31);
    expect(await page.locator('#entlist').evaluate(el => el.scrollTop)).toBe(0);
    expect(pageErrors).toEqual([]);
  });

  test('node detail panel shows closed source document rows, sends docs=brief, and its button opens the entity list row', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    const nodeRequests = [];
    page.on('request', request => { if (new URL(request.url()).pathname.endsWith('/node')) nodeRequests.push(request.url()); });
    await page.setViewportSize({ width: 1400, height: 700 });
    await waitForClaire(page);
    await page.locator('#centertab-graph').click();

    const row = page.locator('#entlist .entitem[data-ent-id="ent-list-01"]');
    await row.locator('.entrow').click();
    const panel = page.locator('#panel');
    await expect(panel.locator('h2')).toContainText('목록 엔티티 01');
    expect(nodeRequests.length).toBe(1);
    expect(nodeRequests[0]).toContain('docs=brief');

    // The panel has only the source document section (anonymous session). 관찰/주장 and 연결 are in the list row.
    await expect(panel.locator('h3')).toHaveText(['출처 문서 (40)']);
    await expect(panel.locator('#node-listrow-btn')).toHaveText('관찰/주장 · 관계 보기 (8)');

    // 31 rows first, the newest document first, all closed. The filter field shows because there are more than 31 documents.
    const srcRows = panel.locator('.srcrow');
    await expect(srcRows).toHaveCount(31);
    await expect(panel.locator('#srcfilter')).toBeVisible();
    await expect(srcRows.nth(0).locator('.srcname')).toHaveText('페이징 자료 01');
    await expect(srcRows.nth(0).locator('.srcdate')).toHaveText(/^\d{4}-\d{2}-\d{2}$/);
    await expect(srcRows.nth(0).locator('.srctype')).toHaveText('web');
    await expect(panel.locator('.srcbody:visible')).toHaveCount(0);
    await expect(panel.locator('.srctitle[aria-expanded="true"]')).toHaveCount(0);

    // A click on the title shows the summary (justified text) and the link to the original page.
    const first = srcRows.nth(0);
    await expect(first.locator('.srcsum')).toBeHidden();
    await first.locator('.srctitle').click();
    await expect(first.locator('.srctitle')).toHaveAttribute('aria-expanded', 'true');
    const bodyId = await first.locator('.srctitle').getAttribute('aria-controls');
    await expect(first.locator('.srcbody')).toHaveAttribute('id', bodyId);
    await expect(first.locator('.srcsum')).toBeVisible();
    await expect(first.locator('.srcsum')).toContainText('출처 문서 요약 문단입니다.');
    expect(await first.locator('.srcsum').evaluate(el => getComputedStyle(el).textAlign)).toBe('justify');
    // CJK justify: a line can break between syllables, thus the extra space does not collect between eojeol.
    expect(await first.locator('.srcsum').evaluate(el => getComputedStyle(el).wordBreak)).toBe('normal');
    await expect(first.locator('.srcbody .srcopen')).toHaveText('본문 열기');
    await expect(first.locator('.srcbody a')).toHaveCount(0);
    await expect(first.locator('.srcread')).toHaveAttribute('aria-label', '본문 보기');

    // More than one row can be open. A document with no summary and no detail shows 요약 없음 and has no reader button.
    const second = srcRows.nth(1);
    await second.locator('.srctitle').click();
    await expect(second.locator('.srcsum')).toHaveText('요약 없음');
    await expect(second.locator('.srcread')).toHaveCount(0);
    await expect(second.locator('.srcbody .srcopen')).toHaveCount(0);
    await expect(panel.locator('.srcbody:visible')).toHaveCount(2);
    await first.locator('.srctitle').click();
    await expect(first.locator('.srcsum')).toBeHidden();

    // A scroll to the end of the panel adds the remaining rows. The node header stays in view.
    await page.locator('#drawerscroll').evaluate(el => { el.scrollTop = el.scrollHeight; });
    await expect(srcRows).toHaveCount(40);
    await expect(panel.locator('.nodehead h2')).toBeInViewport();
    await expect(second.locator('.srcsum')).toBeVisible();

    // The filter field selects rows by title.
    await panel.locator('#srcfilter').fill('자료 4');
    await expect(srcRows.locator('.srcname')).toHaveText(['페이징 자료 40']);
    await panel.locator('#srcfilter').fill('');
    await expect(srcRows).toHaveCount(31);

    // The row is closed and the center view is the reader view. The button of the panel shows the entity list row with no new request.
    await row.locator('.entrow').click();
    await expect(page.locator('#entlist .entitem.open')).toHaveCount(0);
    await page.evaluate(() => setCenterView('reader'));
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
    await panel.locator('#node-listrow-btn').click();
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'entities');
    const opened = page.locator('#entlist .entitem.open');
    await expect(opened).toHaveAttribute('data-ent-id', 'ent-list-01');
    await expect(opened.locator('#entbody h4').first()).toHaveText('관찰/주장');
    await expect(opened.locator('#entbody h4').nth(1)).toHaveText('관계 (8)');
    await expect(opened.locator('.entrow')).toBeInViewport();
    expect(nodeRequests.length).toBe(1);

    // The hover popup also sends docs=brief.
    await page.evaluate(() => showNodePop('ent-list-01', 300, 300));
    await expect.poll(() => nodeRequests.length).toBe(2);
    expect(nodeRequests[1]).toContain('docs=brief');
    await page.evaluate(() => hideNodePop());

    // The reader button of a source document row opens the document in the reader view.
    await srcRows.nth(0).locator('.srcread').click();
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
    await expect(page.locator('#reader')).toContainText('페이징 자료 01의 본문입니다.');

    // The 본문 열기 button inside the expanded source row also opens the document in the reader view.
    await page.evaluate(() => selectEntity('ent-list-01'));
    await srcRows.nth(0).locator('.srctitle').click();
    await srcRows.nth(0).locator('.srcbody .srcopen').click();
    await expect(page.locator('#reader')).toContainText('페이징 자료 01의 본문입니다.');
    expect(pageErrors).toEqual([]);
  });

  test('owner scope shows 더 알아보기 before 출처 문서 in the node detail panel', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1400, height: 800 });
    // A login token of the seed database is for one login. This test has its own token.
    await page.goto('/?t=e2e-panel-token-0123456789abcdef01', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    await expect.poll(() => page.evaluate(() => window.claireDebug?.stabilized)).toBe(true);
    expect(await page.evaluate(() => canWrite())).toBe(true);

    await page.evaluate(() => selectEntity('ent-list-01'));
    const panel = page.locator('#panel');
    await expect(panel.locator('h2')).toContainText('목록 엔티티 01');
    await expect(panel.locator('h3')).toHaveText(['더 알아보기', '출처 문서 (40)']);
    await expect(panel.locator('#rq')).toBeVisible();
    await expect(panel.locator('.srcbody:visible')).toHaveCount(0);
    expect(pageErrors).toEqual([]);
  });
});

test('mobile node detail button opens the list drawer at the entity row', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 800 });
  await waitForClaire(page);
  await page.setViewportSize({ width: 390, height: 800 });
  await page.evaluate(() => { setCenterView('graph'); selectedNodeId = 'ent-hub'; loadNode('ent-hub'); openDetailPane(); });
  await expect(page.locator('#panel h2')).toContainText('허브 엔티티');
  await expect(page.locator('body')).not.toHaveClass(/docdrawer-open/);
  await page.locator('#node-listrow-btn').click();
  await expect(page.locator('body')).toHaveClass(/docdrawer-open/);
  await expect(page.locator('body')).not.toHaveClass(/detail-open/);
  const opened = page.locator('#entlist .entitem.open');
  await expect(opened).toHaveAttribute('data-ent-id', 'ent-hub');
  await expect(opened.locator('#entbody h4').nth(1)).toHaveText('관계 (40)');
  await expect(opened.locator('.entrow')).toBeInViewport();

  // The sticky node header covers the full width and the top padding of the scroll container at the mobile size (padding 14px 16px).
  await page.evaluate(() => closeDocDrawer());
  await expect(page.locator('body')).not.toHaveClass(/docdrawer-open/);
  await page.evaluate(() => { selectedNodeId = 'ent-list-01'; loadNode('ent-list-01'); openDetailPane(); });
  await expect(page.locator('#panel h2')).toContainText('목록 엔티티 01');
  await expect(page.locator('#panel .srcrow')).toHaveCount(31);
  await page.locator('#drawerscroll').evaluate(el => { el.scrollTop = 400; });
  const box = await page.evaluate(() => {
    const sc = document.getElementById('drawerscroll'), s = sc.getBoundingClientRect(), h = document.querySelector('#panel .nodehead').getBoundingClientRect();
    return { scrollTop: sc.scrollTop, padTop: getComputedStyle(sc).paddingTop, padLeft: getComputedStyle(sc).paddingLeft, left: Math.round(h.left - s.left - sc.clientLeft), top: Math.round(h.top - s.top - sc.clientTop), width: Math.round(h.width), clientWidth: sc.clientWidth };
  });
  expect(box.scrollTop).toBeGreaterThan(300);
  expect(box.padTop).toBe('14px');
  expect(box.padLeft).toBe('16px');
  expect(box.left).toBe(0);
  expect(box.top).toBe(0);
  expect(box.width).toBe(box.clientWidth);
  expect(pageErrors).toEqual([]);
});

test('home view opens by default with facts, recent ingestion, list switch, and on-demand context panel', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page.locator('#homewrap')).toBeVisible();
  await expect(page.locator('#home-facts')).not.toBeEmpty();
  await expect(page.locator('#home-recent')).toBeVisible();
  await expect(page.locator('#home-recent-list .home-row').first()).toBeVisible();
  await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
  await expect(page.locator('body')).toHaveClass(/ctx-closed/);
  await expect(page.locator('#bar #ctxbtn')).toHaveCount(0);

  // The list switch toggles between Entities and Documents in the same view, and resets on the next view change.
  await page.locator('#listswitch .list-switch-btn[data-list="entities"]').click();
  await expect(page.locator('#docs')).toHaveAttribute('data-list', 'entities');
  await page.locator('#centertab-graph').click();
  await expect(page.locator('#docs')).toHaveAttribute('data-list', 'entities');
  await page.locator('#centertab-home').click();
  await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');

  // Opening a recent ingestion item moves to the reader and opens the context panel on demand.
  await page.locator('#home-recent-list .home-row').first().click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
  await expect(page.locator('#reader')).toBeVisible();
  await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
  await expect(page.locator('#detailclose')).toBeVisible();
  await expect(page.locator('#rctxbtn')).toBeHidden();

  // Returning via brand link restores the home view and closes the context panel.
  await page.locator('a.brand').click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page).toHaveTitle('Claire Bible — 홈');
  await expect(page.locator('body')).toHaveClass(/ctx-closed/);
  expect(pageErrors).toEqual([]);
});

test('narrow screen botnav switches views and opens list drawer and context bottom sheet as dialogs', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 390, height: 844 });
  await waitForClaire(page);
  await expectNoHorizontalOverflow(page);

  const botnav = page.locator('#botnav');
  await expect(botnav).toBeVisible();
  // The view buttons are in the bottom bar. The button of the command palette stays in the header.
  await expect(page.locator('#centertab-group')).toBeHidden();
  await expect(page.locator('#palettetrigger')).toBeVisible();
  await expect(page.locator('#botnav-home')).toBeVisible();
  await expect(page.locator('#botnav-list')).toBeVisible();
  await expect(page.locator('#botnav-reader')).toBeVisible();
  await expect(page.locator('#botnav-graph')).toBeVisible();
  await expect(page.locator('#botnav-context')).toBeVisible();

  await page.locator('#botnav-home').click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page.locator('#homewrap')).toBeVisible();
  await expect(page.locator('#botnav-home')).toHaveAttribute('aria-current', 'page');

  // Open list drawer, check modal dialog semantics and inert center, then close via Escape.
  const listBtn = page.locator('#botnav-list');
  await listBtn.click();
  const docs = page.locator('#docs');
  await expect(docs).toBeVisible();
  await expect(docs).toHaveAttribute('role', 'dialog');
  await expect(docs).toHaveAttribute('aria-modal', 'true');
  await expect(listBtn).toHaveAttribute('aria-expanded', 'true');
  expect(await page.locator('#centerwrap').evaluate(el => el.inert)).toBe(true);
  await page.keyboard.press('Escape');
  await expect(docs).toBeHidden();
  await expect(listBtn).toHaveAttribute('aria-expanded', 'false');
  expect(await docs.getAttribute('role')).toBeNull();
  expect(await page.locator('#centerwrap').evaluate(el => el.inert)).toBe(false);
  await expect(listBtn).toBeFocused();

  // Open context bottom sheet above #botnav, check sheet geometry and dialog role, then close via Escape.
  const ctxNavBtn = page.locator('#botnav-context');
  await ctxNavBtn.click();
  const detailPane = page.locator('#detailpane');
  await expect(detailPane).toBeVisible();
  await expect(detailPane).toHaveAttribute('role', 'dialog');
  await expect(detailPane).toHaveAttribute('aria-modal', 'true');
  await expect(ctxNavBtn).toHaveAttribute('aria-expanded', 'true');
  expect(await page.locator('#centerwrap').evaluate(el => el.inert)).toBe(true);
  const navBox = await botnav.boundingBox();
  expect(navBox).not.toBeNull();
  await expect.poll(async () => {
    const b = await detailPane.boundingBox();
    return b ? Math.round(b.y + b.height) : 9999;
  }).toBeLessThanOrEqual(Math.round(navBox.y + 2));
  const sheetBox = await detailPane.boundingBox();
  expect(sheetBox).not.toBeNull();
  expect(sheetBox.height).toBeGreaterThan(844 * 0.55);
  await page.keyboard.press('Escape');
  await expect(detailPane).toBeHidden();
  await expect(ctxNavBtn).toHaveAttribute('aria-expanded', 'false');
  expect(await detailPane.getAttribute('role')).toBeNull();
  expect(await page.locator('#centerwrap').evaluate(el => el.inert)).toBe(false);
  await expect(ctxNavBtn).toBeFocused();
  expect(pageErrors).toEqual([]);
});

test('home overview knowledge map lists, entity context selection, mini map, and owner maintenance card', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });
  await waitForClaire(page);

  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page.locator('#home-map')).toBeVisible();
  await expect(page.locator('#home-decisions')).toBeHidden();
  await expect(page.locator('#home-maint')).toBeHidden();
  await expect(page.locator('#format-warn-banner')).toBeHidden();

  const hubRows = page.locator('#home-map-hubs-rows .home-map-row');
  await expect(hubRows).toHaveCount(5);
  await expect(hubRows.nth(0)).toContainText('허브 엔티티');
  await expect(hubRows.nth(0)).toContainText('40');
  await expect(hubRows.nth(1)).toContainText('목록 엔티티 01');
  await expect(hubRows.nth(1)).toContainText('8');

  const bridgeRows = page.locator('#home-map-bridges-rows .home-map-row');
  await expect(bridgeRows).toHaveCount(4);
  await expect(bridgeRows.nth(0)).toContainText('목록 엔티티 01');
  await expect(bridgeRows.nth(0)).toContainText('40');

  await expect(page.locator('#home-map-col-recent')).toBeVisible();
  await expect(page.locator('#home-map-recent-rows .home-map-row')).toHaveCount(5);

  const miniBox = await page.locator('#home-minimap-canvas').boundingBox();
  expect(miniBox).not.toBeNull();
  expect(miniBox.width).toBeGreaterThan(50);
  expect(miniBox.height).toBeGreaterThan(50);

  // Clicking an entity row opens the context panel without leaving the home view.
  await hubRows.nth(0).click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
  await expect(page.locator('#panel')).toContainText('허브 엔티티');

  // Clicking the mini map opens the interactive graph view.
  await page.locator('#home-minimap').click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');
  await expect(page.locator('#netwrap')).toBeVisible();

  // Authenticate as owner and check the maintenance card while the format banner stays hidden.
  // A login token of the seed database is for one login. This test has its own token.
  await page.goto('/?t=e2e-home-token-0123456789abcdef012', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await expect(page.locator('#home-maint')).toBeVisible();
  await expect(page.locator('#format-warn-banner')).toBeHidden();

  const fs = await page.evaluate(() => window.currentFormatStatus);
  expect(fs).not.toBeNull();
  expect(fs.missing_detail_docs).toBeGreaterThan(0);
  const mismatched = fs.mismatched != null ? fs.mismatched : fs.mismatched_docs;
  const expectedMaintCount = (fs.missing_detail_docs > 0 ? 1 : 0) + (mismatched > 0 ? 1 : 0);

  const maintRows = page.locator('#home-maint-list .home-maint-row');
  await expect(maintRows).toHaveCount(expectedMaintCount);
  await expect(maintRows.nth(0)).toContainText(String(fs.missing_detail_docs));
  await expect(maintRows.nth(0)).toContainText('./cb-manuscript app format-migrate --apply');
  if (mismatched > 0) {
    await expect(maintRows.nth(1)).toContainText(String(mismatched));
    await expect(maintRows.nth(1)).toContainText('./cb-manuscript app format-migrate --apply');
  }
  expect(pageErrors).toEqual([]);
});

test.describe('document context panel (HW-3)', () => {
  test.use({ serviceWorkers: 'block' });

  test('document context panel renders Links, Entities, and Source tabs with keyboard navigation (HW-3)', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1280, height: 800 });
    await waitForClaire(page);

    // 1. Open doc-page-1 from the document list (has summary, detail, and shared entity across 40 docs)
    const docPage1 = page.locator('#doclist .docitem').filter({ hasText: '페이징 자료 01' });
    await expect(docPage1).toBeVisible();
    await docPage1.click();

    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
    await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
    const tablist = page.locator('#panel [role="tablist"]');
    await expect(tablist).toBeVisible();
    const tabs = tablist.locator('[role="tab"]');
    await expect(tabs).toHaveCount(3);

    const tabLinks = page.locator('#ctx-tab-links');
    const tabEntities = page.locator('#ctx-tab-entities');
    const tabSource = page.locator('#ctx-tab-source');
    const tabPanel = page.locator('#ctx-tabpanel-doc');

    await expect(tabLinks).toHaveAttribute('aria-selected', 'true');
    await expect(tabLinks).toHaveAttribute('tabindex', '0');
    await expect(tabEntities).toHaveAttribute('aria-selected', 'false');
    await expect(tabEntities).toHaveAttribute('tabindex', '-1');
    await expect(tabPanel).toHaveAttribute('role', 'tabpanel');
    await expect(tabPanel).toHaveAttribute('aria-labelledby', 'ctx-tab-links');

    // Verify active tab has a visible bottom border highlight
    const borderWidth = await tabLinks.evaluate(el => getComputedStyle(el).borderBottomWidth);
    const borderColor = await tabLinks.evaluate(el => getComputedStyle(el).borderBottomColor);
    expect(borderWidth).toBe('2px');
    expect(borderColor).not.toBe('rgba(0, 0, 0, 0)');

    // Links tab shows top 5 documents sharing entities with shared count badges
    const sharedRows = tabPanel.locator('.ctx-shared-row');
    await expect(sharedRows).toHaveCount(5);
    await expect(sharedRows.first().locator('.prov.det')).toContainText('공유 1');

    // 2. Keyboard navigation: ArrowRight -> Entities, ArrowRight -> Source, Home -> Links, End -> Source, ArrowLeft -> Entities
    await tabLinks.focus();
    await page.keyboard.press('ArrowRight');
    await expect(tabEntities).toHaveAttribute('aria-selected', 'true');
    await expect(tabEntities).toBeFocused();
    await expect(tabPanel).toHaveAttribute('aria-labelledby', 'ctx-tab-entities');
    await expect(tabPanel.locator('.ctx-entity-row')).toHaveCount(1);
    await expect(tabPanel.locator('.ctx-entity-row').first()).toContainText('목록 엔티티 01');

    await page.keyboard.press('ArrowRight');
    await expect(tabSource).toHaveAttribute('aria-selected', 'true');
    await expect(tabSource).toBeFocused();
    await expect(tabPanel).toHaveAttribute('aria-labelledby', 'ctx-tab-source');
    await expect(tabPanel).toContainText('원문');
    await expect(tabPanel).toContainText('모델 생성물');
    await expect(tabPanel).toContainText('요약');
    await expect(tabPanel).toContainText('있음');

    await page.keyboard.press('Home');
    await expect(tabLinks).toHaveAttribute('aria-selected', 'true');
    await expect(tabLinks).toBeFocused();
    await page.keyboard.press('End');
    await expect(tabSource).toHaveAttribute('aria-selected', 'true');
    await expect(tabSource).toBeFocused();
    await page.keyboard.press('ArrowLeft');
    await expect(tabEntities).toHaveAttribute('aria-selected', 'true');
    await expect(tabEntities).toBeFocused();

    // 3. Open doc-1 -> verify Entities tab lists degree + clicking an entity opens entity detail with backlink to document
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).click();
    await expect(tabEntities).toHaveAttribute('aria-selected', 'true');
    const entRows = tabPanel.locator('.ctx-entity-row');
    await expect(entRows).toHaveCount(2);
    await entRows.first().click();

    await expect(page.locator('#panel .backlink')).toBeVisible();
    await expect(page.locator('#panel .backlink')).toContainText('문서로 돌아가기');
    await page.locator('#panel .backlink').click();
    await expect(tablist).toBeVisible();

    // 4. Verify Source badge (full and partial), resolution stage classes, and decision stream link on doc-2
    let doc2Mode = 'full';
    let doc2Provider = 'gemini';
    let doc2Model = 'gemini-test-1';
    await page.route(url => url.pathname.endsWith('/document'), async (route) => {
      const id = new URL(route.request().url()).searchParams.get('id');
      const response = await route.fetch();
      const json = await response.json();
      if (id === 'doc-2') {
        // The provider and the model of the latest extraction.
        json.extraction_provider = doc2Provider;
        json.extraction_model = doc2Model;
        json.has_transcript = true;
        if (doc2Mode === 'full') {
          json.orig_chars = 1200;
          json.raw_chars = 1200;
          json.raw_truncated = false;
        } else {
          json.orig_chars = 12000;
          json.raw_chars = 4800;
          json.raw_truncated = true;
          json.references_truncated = true;
        }
      }
      await route.fulfill({ status: response.status(), contentType: 'application/json', body: JSON.stringify(json) });
    });

    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 2' }).click();
    await expect(page.locator('#reader .docmeta .source-badge[data-source-badge="full"].prov.det')).toHaveText('원문 보존');
    await tabSource.click();
    await expect(tabPanel.locator('.source-badge[data-source-badge="full"].prov.det')).toHaveText('원문 보존');
    await expect(tabPanel.locator('.prov.det', { hasText: '규칙 일치' })).toBeVisible();
    // The model of the latest extraction shows in the Source tab and in the reader meta line, to the right of the source badge. The summary and the detail have no "model output" label.
    await expect(page.locator('#ctx-src-model')).toHaveText('gemini · gemini-test-1');
    const modelBadge = page.locator('#reader .docmeta .model-badge.prov.det');
    await expect(modelBadge).toHaveText('gemini-test-1');
    await expect(modelBadge).toHaveAttribute('title', /모델/);
    const ccBadgeBox = await page.locator('#reader .docmeta .cc-tag').boundingBox();
    const fullBadgeBox = await page.locator('#reader .docmeta .source-badge').boundingBox();
    const modelBadgeBox = await modelBadge.boundingBox();
    expect(modelBadgeBox.x).toBeGreaterThan(fullBadgeBox.x);
    expect(Math.abs(ccBadgeBox.height - fullBadgeBox.height)).toBeLessThanOrEqual(0.5);
    expect(Math.abs(fullBadgeBox.height - modelBadgeBox.height)).toBeLessThanOrEqual(0.5);
    expect(Math.abs(fullBadgeBox.height - 22)).toBeLessThanOrEqual(0.5);
    await expect(page.locator('#rbody .prov.model')).toHaveCount(0);
    // Unknown stage 'semantic_high' must render as plain text without .prov class
    await expect(tabPanel.locator('dt span:not(.prov)', { hasText: 'semantic_high' })).toBeVisible();

    // Agent-client extraction without a recorded model must never fall back to displaying raw 'agent_client'
    doc2Provider = 'agent_client';
    doc2Model = null;
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).click();
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 2' }).click();
    await expect(page.locator('#reader .docmeta .model-badge')).toHaveCount(0);
    await expect(page.locator('#ctx-src-model')).toHaveCount(0);

    // Agent-client extraction with a recorded model renders the model name without 'agent_client'
    doc2Provider = 'agent_client';
    doc2Model = 'gemini-3.8-flash';
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).click();
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 2' }).click();
    await expect(page.locator('#reader .docmeta .model-badge')).toHaveText('gemini-3.8-flash');
    await expect(page.locator('#ctx-src-model')).toHaveText('gemini-3.8-flash');

    // Check partial source badge with character counts on reload of doc-2
    doc2Mode = 'partial';
    doc2Provider = 'gemini';
    doc2Model = 'gemini-test-1';
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).click();
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 2' }).click();
    await expect(page.locator('#reader .docmeta .source-badge[data-source-badge="partial"].prov.det')).toContainText('원문 일부 보존');
    await expect(page.locator('#reader .docmeta .source-badge[data-source-badge="partial"].prov.det')).toContainText('4,800 / 12,000');
    await expect(tabPanel.locator('.source-badge[data-source-badge="partial"].prov.det')).toContainText('원문 일부 보존 (4,800 / 12,000)');
    // One notice for a cut source text: the source badge has the counts and its hover text has the reason. The old truncation tag does not show.
    const partialBadge = page.locator('#reader .docmeta .source-badge[data-source-badge="partial"]');
    await expect(partialBadge).toHaveAttribute('data-trunc', 'references');
    await expect(partialBadge).toHaveAttribute('title', /참고문헌/);
    await expect(page.locator('#reader .docmeta .trunc-references')).toHaveCount(0);
    await expect(page.locator('#reader .docmeta').getByText('4,800 / 12,000')).toHaveCount(1);

    // Decision stream link in Source tab switches to stream view
    await tabPanel.locator('.ctx-stream-link').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'stream');

    expect(pageErrors).toEqual([]);
  });

  test('HW-4: local graph renders Ring 1 and Ring 2, line grammar, limits, accessible buttons, selection, and cross-view hover synchronization', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1280, height: 800 });
    await waitForClaire(page);

    // 1. Select document 1 in document list and verify local graph in Links tab
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).click();
    const tabLinks = page.locator('#ctx-tab-links');
    await tabLinks.click();
    const localGraphSvg = page.locator('#ctx-local-graph-slot svg.ctx-local-graph');
    await expect(localGraphSvg).toBeVisible();
    await expect(localGraphSvg).toHaveAttribute('role', 'region');

    // Document center node at (0, 0)
    const centerNode = localGraphSvg.locator('.ctx-lg-center');
    await expect(centerNode).toBeVisible();

    // Ring 1 entities (ent-1 and ent-2)
    const ring1Nodes = localGraphSvg.locator('.ctx-lg-node.ring-1');
    await expect(ring1Nodes).toHaveCount(2);

    // Accessibility tree: verify role="button", tabindex="0", and aria-label
    const ent1Btn = localGraphSvg.locator('.ctx-lg-node[data-entity-id="ent-1"]');
    await expect(ent1Btn).toHaveAttribute('role', 'button');
    await expect(ent1Btn).toHaveAttribute('tabindex', '0');
    await expect(ent1Btn).toHaveAttribute('aria-label', /엔티티 A|별칭 A1/);

    // Thin line from document center to Ring 1 node
    const docLines = localGraphSvg.locator('.ctx-lg-doc-edge');
    await expect(docLines).toHaveCount(2);

    // 2. Entity selection via click triggers GET /node and opens entity details without mutation
    const [nodeReq] = await Promise.all([
      page.waitForRequest(req => req.url().includes('/node?id=ent-1') && req.method() === 'GET'),
      ent1Btn.click(),
    ]);
    expect(nodeReq.method()).toBe('GET');
    await expect(page.locator('#panel .backlink')).toBeVisible();
    await page.locator('#panel .backlink').click();

    // 3. Keyboard navigation: Tab / Enter selection
    await tabLinks.click();
    await ent1Btn.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#panel .backlink')).toBeVisible();
    await page.locator('#panel .backlink').click();

    // 4. Cross-view hover synchronization: local graph, Entities tab, and reader marks
    await tabLinks.click();
    await ent1Btn.hover();
    await expect(localGraphSvg).toHaveClass(/has-hover/);
    await expect(ent1Btn).toHaveClass(/hover-active/);

    // Mouse leave clears hover state on local graph
    await page.locator('#ctx-tabpanel-doc').hover({ position: { x: 5, y: 5 } });
    await expect(localGraphSvg).not.toHaveClass(/has-hover/);
    await expect(ent1Btn).not.toHaveClass(/hover-active/);

    // Switch to Entities tab and hover an entity row
    const tabEntities = page.locator('#ctx-tab-entities');
    await tabEntities.click();
    const entRow = page.locator('.ctx-entity-row[data-entity-id="ent-1"]');
    await entRow.hover();
    await expect(entRow).toHaveClass(/entity-hover-active/);

    // Mouse leave clears hover state on entity row
    await page.locator('#ctx-tabpanel-doc').hover({ position: { x: 5, y: 5 } });
    await expect(entRow).not.toHaveClass(/entity-hover-active/);

    await tabLinks.click();

    // 5. Mock larger document and graph to verify Ring 1 (max 24), Ring 2 (max 12), line grammar, and unshown count text
    const mockNodes = [];
    for (let i = 1; i <= 26; i++) {
      mockNodes.push({ id: `mock-ent-${i}`, label: `Mock Ent ${i}`, group: 'Concept', degree: 30 - i, observations: [] });
    }
    const mockEdges = [];
    for (let j = 1; j <= 15; j++) {
      mockEdges.push({
        id: `e-ext-${j}`,
        from: 'mock-ent-1',
        to: `ext-ent-${j}`,
        label: 'relates_to',
        dashes: j % 2 === 1,
      });
    }

    await page.evaluate(({ nodes, edges }) => {
      const mockDoc = {
        id: 'doc-mock-rings',
        title: 'Mock Document Rings',
        source_type: 'web',
        nodes: nodes,
      };
      const debugAllNodes = window.claireDebug?.allNodes;
      const debugAllEdges = window.claireDebug?.allEdges;
      if (debugAllNodes && typeof debugAllNodes.add === 'function') {
        nodes.forEach(n => {
          if (!debugAllNodes.get(n.id)) debugAllNodes.add(n);
        });
        for (let j = 1; j <= 15; j++) {
          if (!debugAllNodes.get(`ext-ent-${j}`)) {
            debugAllNodes.add({ id: `ext-ent-${j}`, label: `External Ent ${j}`, degree: 20 - j, group: 'Tool' });
          }
        }
      }
      if (debugAllEdges && typeof debugAllEdges.add === 'function') {
        edges.forEach(e => {
          if (!debugAllEdges.get(e.id)) debugAllEdges.add(e);
        });
      }
      renderDocPanel(mockDoc);
    }, { nodes: mockNodes, edges: mockEdges });

    // Ring 1 shows max 24 entities
    const mockedRing1Nodes = page.locator('#ctx-local-graph-slot svg.ctx-local-graph .ctx-lg-node.ring-1');
    await expect(mockedRing1Nodes).toHaveCount(24);

    // Ring 2 shows max 12 entities
    const mockedRing2Nodes = page.locator('#ctx-local-graph-slot svg.ctx-local-graph .ctx-lg-node.ring-2');
    await expect(mockedRing2Nodes).toHaveCount(12);

    // Total unshown entities text: 2 unshown in Ring 1 (26 - 24) + 3 unshown in Ring 2 (15 - 12) = 5
    const unshownText = page.locator('.ctx-local-graph-unshown');
    await expect(unshownText).toBeVisible();
    await expect(unshownText).toContainText('5');

    // Visual line grammar: solid lines for standard relations, dashed lines for provisional relations
    const provisionalEdge = page.locator('#ctx-local-graph-slot svg.ctx-local-graph .ctx-lg-edge.provisional').first();
    await expect(provisionalEdge).toBeAttached();
    await expect(provisionalEdge).toHaveAttribute('stroke-dasharray', '3 3');

    const solidEdge = page.locator('#ctx-local-graph-slot svg.ctx-local-graph .ctx-lg-edge:not(.provisional)').first();
    await expect(solidEdge).toBeAttached();
    await expect(solidEdge).not.toHaveAttribute('stroke-dasharray');

    expect(pageErrors).toEqual([]);
  });

  test('HW-5: reader text entity marks verify zero DOM mutation (T1), selection without mutation (T2), share page exclusion (T3), and rule validation (T4)', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1280, height: 800 });
    await waitForClaire(page);

    // 1. Open test document 1 in reader
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).click();
    const rbody = page.locator('#rbody');
    await expect(rbody).toBeVisible();
    // The reader empties #rbody while the document loads. The baseline is the rendered text, not the empty state.
    await expect(page.locator('#rbody .doc-content').first()).toBeVisible();
    await expect(page.locator('#reader')).toHaveAttribute('aria-busy', 'false');

    // T1: Strict zero DOM mutation verification on #rbody
    const baselineInnerHTML = await page.evaluate(() => document.getElementById('rbody').innerHTML);
    const baselineTextContent = await page.evaluate(() => document.getElementById('rbody').textContent);

    // Turn marks off via localStorage
    await page.evaluate(() => {
      localStorage.setItem('claireEntityMarks', '0');
      applyEntityMarks(curReaderDocData);
    });
    const offInnerHTML = await page.evaluate(() => document.getElementById('rbody').innerHTML);
    const offTextContent = await page.evaluate(() => document.getElementById('rbody').textContent);
    expect(offInnerHTML).toBe(baselineInnerHTML);
    expect(offTextContent).toBe(baselineTextContent);

    // Turn marks on via localStorage
    await page.evaluate(() => {
      localStorage.setItem('claireEntityMarks', '1');
      applyEntityMarks(curReaderDocData);
    });
    const onInnerHTML = await page.evaluate(() => document.getElementById('rbody').innerHTML);
    const onTextContent = await page.evaluate(() => document.getElementById('rbody').textContent);
    expect(onInnerHTML).toBe(baselineInnerHTML);
    expect(onTextContent).toBe(baselineTextContent);

    // Legend #rlegend is located outside #rbody
    const isLegendOutsideRbody = await page.evaluate(() => {
      const leg = document.getElementById('rlegend');
      const b = document.getElementById('rbody');
      return leg !== null && !b.contains(leg);
    });
    expect(isLegendOutsideRbody).toBe(true);

    // T2: Click marked entity text triggers GET /node with zero POST requests
    const postRequests = [];
    page.on('request', req => {
      if (req.method() === 'POST') postRequests.push(req.url());
    });

    // Set sample text with candidate match to verify pointer selection
    await page.evaluate(() => {
      const testDoc = {
        id: 'doc-t2',
        title: 'T2 Test Doc',
        nodes: [{ id: 'ent-1', label: '엔티티 A', group: 'Concept', aliases: [] }],
      };
      const b = document.getElementById('rbody');
      b.innerHTML = '<div class="doc-content"><p id="test-p">여기에서 엔티티 A 대상 문장이 있습니다.</p></div>';
      applyEntityMarks(testDoc);
    });

    const [nodeGetReq] = await Promise.all([
      page.waitForRequest(req => req.url().includes('/node?id=ent-1') && req.method() === 'GET'),
      page.evaluate(() => {
        const p = document.getElementById('test-p');
        const textNode = p.firstChild;
        const range = document.createRange();
        const start = textNode.nodeValue.indexOf('엔티티 A');
        range.setStart(textNode, start);
        range.setEnd(textNode, start + 5);
        const rect = range.getBoundingClientRect();
        document.getElementById('rbody').dispatchEvent(new MouseEvent('click', {
          clientX: rect.left + 5,
          clientY: rect.top + 5,
          bubbles: true
        }));
      }),
    ]);
    expect(nodeGetReq.method()).toBe('GET');
    expect(postRequests).toEqual([]);

    // T3: Entity marks must not appear on share page
    await page.goto('/silvercord/23456789abcdefgh', { waitUntil: 'domcontentloaded' });
    const shareHighlights = await page.evaluate(() => {
      if (typeof CSS !== 'undefined' && CSS.highlights && CSS.highlights.has('claire-entity')) {
        return CSS.highlights.get('claire-entity').size;
      }
      return 0;
    });
    expect(shareHighlights).toBe(0);

    // T4: Match engine rules (ASCII boundary, 2-3 char acronyms, longest match, collision suppression, and limits)
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await waitForClaire(page);

    const matchEvaluation = await page.evaluate(() => {
      const docT4 = {
        id: 'doc-t4',
        title: 'T4 Match Validation',
        nodes: [
          { id: 'e-go', label: 'Go', group: 'Concept', aliases: [] },
          { id: 'e-ai', label: 'AI', group: 'Concept', aliases: [] },
          { id: 'e-fedify', label: 'Fedify', group: 'Tool', aliases: [] },
          { id: 'e-ge', label: 'Graph Engine', group: 'Concept', aliases: [] },
          { id: 'e-g', label: 'Graph', group: 'Concept', aliases: [] },
          { id: 'e-c1', label: 'Entity 1', group: 'Concept', aliases: ['SharedCollision'] },
          { id: 'e-c2', label: 'Entity 2', group: 'Concept', aliases: ['SharedCollision'] },
          { id: 'e-short', label: 'X', group: 'Concept', aliases: [] },
        ],
      };
      const b = document.getElementById('rbody');
      b.innerHTML = '<div class="doc-content"><p id="t4-p">We use Go and AI in Fedify에서. Do not match go or ai in BioGraph or Graphics. Test Graph Engine and SharedCollision. Also X.</p></div>';
      applyEntityMarks(docT4);

      const markTexts = [];
      const ranges = (typeof CSS !== 'undefined' && CSS.highlights && CSS.highlights.get('claire-entity')) ? [...CSS.highlights.get('claire-entity')] : [];
      ranges.forEach(r => {
        markTexts.push(r.toString());
      });

      return {
        markTexts: markTexts,
        hasGo: markTexts.includes('Go'),
        hasLowercaseGo: markTexts.includes('go'),
        hasAI: markTexts.includes('AI'),
        hasLowercaseAI: markTexts.includes('ai'),
        hasFedify: markTexts.includes('Fedify'),
        hasGraphEngine: markTexts.includes('Graph Engine'),
        hasBioGraphMatch: markTexts.includes('BioGraph'),
        hasCollision: markTexts.includes('SharedCollision'),
        hasShortChar: markTexts.includes('X'),
      };
    });

    // 2-3 ASCII characters match case-sensitively
    expect(matchEvaluation.hasGo).toBe(true);
    expect(matchEvaluation.hasLowercaseGo).toBe(false);
    expect(matchEvaluation.hasAI).toBe(true);
    expect(matchEvaluation.hasLowercaseAI).toBe(false);

    // ASCII boundaries allow Korean particles (Fedify에서)
    expect(matchEvaluation.hasFedify).toBe(true);

    // ASCII boundaries reject substring inside alphanumeric word (BioGraph)
    expect(matchEvaluation.hasBioGraphMatch).toBe(false);

    // Longest match first and overlap suppression
    expect(matchEvaluation.hasGraphEngine).toBe(true);

    // Collision suppression: candidate belonging to multiple entities receives no mark
    expect(matchEvaluation.hasCollision).toBe(false);

    // Names < 2 characters dropped
    expect(matchEvaluation.hasShortChar).toBe(false);

    // Safety limits: text > 400,000 characters or candidates > 300 names
    const limitsEvaluation = await page.evaluate(() => {
      const giantDoc = {
        id: 'doc-giant',
        nodes: [{ id: 'e-sample', label: 'Sample', group: 'Concept', aliases: [] }],
      };
      const b = document.getElementById('rbody');
      b.innerHTML = '<div class="doc-content"><p>' + 'A'.repeat(400005) + '</p></div>';
      applyEntityMarks(giantDoc);
      const giantMarksCount = (typeof CSS !== 'undefined' && CSS.highlights && CSS.highlights.get('claire-entity')) ? CSS.highlights.get('claire-entity').size : 0;
      const legendText = document.getElementById('rlegend')?.textContent || '';

      const manyCandidatesDoc = {
        id: 'doc-many',
        nodes: Array.from({ length: 305 }, (_, i) => ({ id: `e-${i}`, label: `EntityCandidate${i}`, group: 'Concept', aliases: [] })),
      };
      b.innerHTML = '<div class="doc-content"><p>Sample text for entity marking limits test.</p></div>';
      applyEntityMarks(manyCandidatesDoc);
      const manyMarksCount = (typeof CSS !== 'undefined' && CSS.highlights && CSS.highlights.get('claire-entity')) ? CSS.highlights.get('claire-entity').size : 0;

      return {
        giantMarksCount,
        manyMarksCount,
        legendHasLimitNotice: legendText.length > 0,
      };
    });

    expect(limitsEvaluation.giantMarksCount).toBe(0);
    expect(limitsEvaluation.manyMarksCount).toBe(0);
    expect(limitsEvaluation.legendHasLimitNotice).toBe(true);

    expect(pageErrors).toEqual([]);
  });

  test('the reader shows the summary as plain text and the body from the server HTML, and the page has no AsciiDoc converter', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1280, height: 800 });
    await waitForClaire(page);

    // The server is the only AsciiDoc converter: the page has the display helpers and no converter function.
    expect(await page.evaluate(() => ['convertAsciidocToHtml', 'renderContent', 'renderAsciidoc', 'inlineAdocFormat'].filter(name => typeof window[name] !== 'undefined'))).toEqual([]);
    expect(await page.evaluate(() => typeof window.renderPlainText + ' ' + typeof window.applyMathRendering)).toBe('function function');

    // doc-page-1 has a summary and a body: the summary is one paragraph of plain text, the body is the HTML of the server.
    await page.locator('#doclist .docitem').filter({ hasText: '페이징 자료 01' }).click();
    await expect(page.locator('#reader')).toHaveAttribute('aria-busy', 'false');
    const blocks = page.locator('#rbody .doc-content');
    await expect(blocks).toHaveCount(2);
    await expect(blocks.nth(0).locator('p')).toHaveCount(1);
    await expect(blocks.nth(0)).toContainText('출처 문서 요약 문단입니다.');
    await expect(blocks.nth(1).locator('p')).toHaveText('페이징 자료 01의 본문입니다.');

    // doc-1 has nine "==" sections: the headings are in the HTML that the server made.
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).click();
    await expect(page.locator('#rbody .doc-content h2')).toHaveCount(9);
    await expect(page.locator('#rbody .doc-content h2').first()).toHaveText('섹션 1');

    expect(pageErrors).toEqual([]);
  });

  test('HW-6: verify graph view insight lists, cross-view entity detail, backlink, and escape key deselect', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1280, height: 800 });
    await waitForClaire(page);

    // 1. In graph view without selection, context panel displays the three insight lists
    await page.locator('#centertab-graph').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');
    await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
    await expect(page.locator('#ctx-insights')).toBeVisible();

    const hubRows = page.locator('#ctx-insights-hubs-rows .ctx-insight-row');
    await expect(hubRows).toHaveCount(5);
    await expect(hubRows.nth(0)).toContainText('허브 엔티티');
    await expect(hubRows.nth(0)).toContainText('40');
    await expect(hubRows.nth(1)).toContainText('목록 엔티티 01');
    await expect(hubRows.nth(1)).toContainText('8');

    const bridgeRows = page.locator('#ctx-insights-bridges-rows .ctx-insight-row');
    await expect(bridgeRows).toHaveCount(4);
    await expect(bridgeRows.nth(0)).toContainText('목록 엔티티 01');
    await expect(bridgeRows.nth(0)).toContainText('40');

    await expect(page.locator('#ctx-insights-col-recent')).toBeVisible();
    await expect(page.locator('#ctx-insights-recent-rows .ctx-insight-row')).toHaveCount(5);

    // 2. Click entity in insight list opens entity detail without changing center view
    await hubRows.nth(0).click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');
    await expect(page.locator('#panel h2')).toContainText('허브 엔티티');
    await expect(page.locator('#panel .backlink')).toBeHidden();

    // 3. Press Escape in graph view deselects entity and restores insight lists
    await page.keyboard.press('Escape');
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');
    await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
    await expect(page.locator('#ctx-insights')).toBeVisible();
    await expect(page.locator('#ctx-insights-hubs-rows .ctx-insight-row')).toHaveCount(5);

    // 4. In home view, clicking an entity list row opens entity detail in context panel and preserves home view
    await page.locator('#centertab-home').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).toHaveClass(/ctx-closed/);

    await page.locator('#listswitch .list-switch-btn[data-list="entities"]').click();
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'entities');
    const entListRows = page.locator('#entlist .entrow');
    await expect(entListRows.first()).toBeVisible();

    await entListRows.first().click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
    await expect(page.locator('#panel h2')).toContainText('허브 엔티티');

    // Press Escape deselects entity in home view and closes context panel
    await page.keyboard.press('Escape');
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).toHaveClass(/ctx-closed/);

    // 5. In reader view, entity detail displays back link to document context panel
    await page.locator('#centertab-reader').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');

    await page.locator('#listswitch .list-switch-btn[data-list="docs"]').click();
    await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).click();
    await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);

    const tabEntities = page.locator('#ctx-tab-entities');
    await tabEntities.click();
    const docEntRows = page.locator('.ctx-entity-row');
    await expect(docEntRows).toHaveCount(2);

    await docEntRows.first().click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
    await expect(page.locator('#panel .backlink')).toBeVisible();
    await expect(page.locator('#panel .backlink')).toContainText('문서로 돌아가기');

    // Click back link returns directly to document context and restores focus to active tab
    await page.locator('#panel .backlink').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
    await expect(page.locator('#ctx-tab-entities')).toBeVisible();
    await expect(page.locator('#ctx-tab-entities')).toBeFocused();

    // Select entity again and verify Escape deselects entity and returns to document context
    await page.locator('#ctx-tab-entities').click();
    await docEntRows.first().click();
    await expect(page.locator('#panel .backlink')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
    await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
    await expect(page.locator('#ctx-tab-links')).toBeVisible();

    // 6. Cross-view verification: after opening doc in reader, return to graph view
    await page.locator('#centertab-graph').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');
    await expect(page.locator('#ctx-insights')).toBeVisible();

    // Click entity in graph view: backlink must NOT appear
    await hubRows.nth(0).click();
    await expect(page.locator('#panel h2')).toContainText('허브 엔티티');
    await expect(page.locator('#panel .backlink')).toBeHidden();

    // Press Escape to deselect: insight lists restored
    await page.keyboard.press('Escape');
    await expect(page.locator('#ctx-insights')).toBeVisible();

    // Press Escape again when nothing is selected (clearSelections): insight lists must remain
    await page.keyboard.press('Escape');
    await expect(page.locator('#ctx-insights')).toBeVisible();
    await expect(page.locator('#panel .ctx-doc-head')).toBeHidden();

    // 7. Switching from graph view back to reader view restores the document context panel
    await page.locator('#centertab-reader').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
    await expect(page.locator('#ctx-tab-links')).toBeVisible();

    // 8. Entity selected in reader view hides back link when moving to graph view, and restores it when returning
    await page.locator('#ctx-tab-entities').click();
    await docEntRows.first().click();
    await expect(page.locator('#panel .backlink')).toBeVisible();
    // An earlier test can change the primary label of this entity (alias promotion), thus the expected name comes from the panel.
    const selectedEntityName = (await page.locator('#panel h2').innerText()).trim();
    await page.locator('#centertab-graph').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');
    await expect(page.locator('#panel h2')).toHaveText(selectedEntityName);
    await expect(page.locator('#panel .backlink')).toBeHidden();
    await page.locator('#centertab-reader').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
    await expect(page.locator('#panel .backlink')).toBeVisible();
    await page.locator('#panel .backlink').click();
    await expect(page.locator('#ctx-tab-entities')).toBeVisible();

    // 9. Document selection does not overwrite graph insight lists in graph view
    await page.locator('#centertab-graph').click();
    await expect(page.locator('#ctx-insights')).toBeVisible();
    await page.evaluate(() => setActiveDoc('test-doc-1'));
    await expect(page.locator('#ctx-insights')).toBeVisible();
    await expect(page.locator('#panel .ctx-doc-head')).toBeHidden();
    await page.evaluate(() => setActiveDoc(null));
    await expect(page.locator('#ctx-insights')).toBeVisible();

    // 10. Empty graph fallback displays default hint
    await page.evaluate(() => {
      const backup = allNodes;
      try {
        allNodes = null;
        renderGraphInsights();
      } finally {
        allNodes = backup;
      }
    });
    await expect(page.locator('#ctx-insights')).toBeHidden();
    await expect(page.locator('#panel .hint')).toBeVisible();
    await page.evaluate(() => renderGraphInsights());
    await expect(page.locator('#ctx-insights')).toBeVisible();

    // 11. Cross-view verification: return to home view, select entity, press Escape closes panel
    await page.locator('#centertab-home').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).toHaveClass(/ctx-closed/);

    const homeMapRows = page.locator('#home-map-hubs-rows .home-map-row');
    await expect(homeMapRows.first()).toBeVisible();
    await homeMapRows.first().click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).not.toHaveClass(/ctx-closed/);
    await expect(page.locator('#panel h2')).toContainText('허브 엔티티');
    await expect(page.locator('#panel .backlink')).toBeHidden();

    await page.keyboard.press('Escape');
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).toHaveClass(/ctx-closed/);
    await expect(page.locator('#panel .ctx-doc-head')).toBeHidden();

    expect(pageErrors).toEqual([]);
  });

  test('HW-6: verify mobile sheet behavior for entity details and graph insight lists', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 375, height: 667 });
    await waitForClaire(page);

    // 1. On mobile launch, navigate to home view via bottom navigation bar
    await page.locator('#botnav-home').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).not.toHaveClass(/drawer-open/);

    const homeMapHubRow = page.locator('#home-map-hubs-rows .home-map-row').first();
    await expect(homeMapHubRow).toBeVisible();
    await homeMapHubRow.click();

    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).toHaveClass(/drawer-open/);
    await expect(page.locator('#panel h2')).toContainText('허브 엔티티');

    // Press Escape on mobile deselects entity and closes bottom sheet
    await page.keyboard.press('Escape');
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('body')).not.toHaveClass(/drawer-open/);

    // 2. In mobile graph view, tapping Context opens bottom sheet with insight lists
    await page.locator('#botnav-graph').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');

    await page.locator('#botnav-context').click();
    await expect(page.locator('body')).toHaveClass(/drawer-open/);
    await expect(page.locator('#ctx-insights')).toBeVisible();

    const mobileHubRow = page.locator('#ctx-insights-hubs-rows .ctx-insight-row').first();
    await mobileHubRow.click();
    await expect(page.locator('body')).toHaveClass(/drawer-open/);
    await expect(page.locator('#panel h2')).toContainText('허브 엔티티');

    // First Escape deselects entity and restores insight lists in sheet
    await page.keyboard.press('Escape');
    await expect(page.locator('body')).toHaveClass(/drawer-open/);
    await expect(page.locator('#ctx-insights')).toBeVisible();

    // Second Escape closes the bottom sheet
    await page.keyboard.press('Escape');
    await expect(page.locator('body')).not.toHaveClass(/drawer-open/);

    // 3. Resize screen from desktop to mobile when an entity is selected in graph view
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.locator('#centertab-graph').click();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');
    const desktopHubRow = page.locator('#ctx-insights-hubs-rows .ctx-insight-row').first();
    await desktopHubRow.click();
    await expect(page.locator('#panel h2')).toContainText('허브 엔티티');

    // Resize to mobile viewport and deselect with Escape
    await page.setViewportSize({ width: 375, height: 667 });
    await page.keyboard.press('Escape');
    await page.locator('#botnav-context').click();
    await expect(page.locator('body')).toHaveClass(/drawer-open/);
    await expect(page.locator('#ctx-insights')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.locator('body')).not.toHaveClass(/drawer-open/);

    expect(pageErrors).toEqual([]);
  });

});

test.describe('command palette (HW-7)', () => {

  test('HW-7: Ctrl+K opens palette, filters documents and entities, and runs commands', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1280, height: 800 });
    await waitForClaire(page);

    const dialog = page.locator('#cmdpalette');
    const input = page.locator('#cmdpalette-input');
    await expect(dialog).toBeHidden();

    // 1. Open palette with "/" and close with Escape. The dialog has an opaque background.
    await page.keyboard.press('/');
    await expect(dialog).toBeVisible();
    await expect(input).toBeFocused();
    const paletteBg = await dialog.evaluate(el => getComputedStyle(el).backgroundColor);
    expect(paletteBg).not.toBe('rgba(0, 0, 0, 0)');
    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();

    // 2. Open palette from the hamburger menu and filter for a document.
    await page.locator('#hamburger-btn').click();
    await page.locator('#palettebtn').click();
    await expect(dialog).toBeVisible();

    await input.fill('테스트 문서 1');
    const docRow = page.locator('#cmdpalette-list .cmd-palette-row').filter({ hasText: '테스트 문서 1' }).first();
    await expect(docRow).toBeVisible();
    await docRow.click();

    await expect(dialog).toBeHidden();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
    await expect(page.locator('#rtitle')).toContainText('테스트 문서 1');

    // 3. Reopen palette with the header button, select an entity, and keep the reader view.
    await page.locator('#palettetrigger').click();
    await expect(dialog).toBeVisible();
    await input.fill('허브 엔티티');
    const entRow = page.locator('#cmdpalette-list .cmd-palette-row').filter({ hasText: '허브 엔티티' }).first();
    await expect(entRow).toBeVisible();
    await entRow.click();

    await expect(dialog).toBeHidden();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
    await expect(page.locator('#panel h2')).toContainText('허브 엔티티');

    // 4. Reopen palette with "/" and run the Go to Graph command with Enter.
    await page.keyboard.press('/');
    await expect(dialog).toBeVisible();
    await input.fill('그래프');
    await expect(page.locator('#cmdpalette-list .cmd-palette-row.is-active')).toBeVisible();
    await page.keyboard.press('Enter');

    await expect(dialog).toBeHidden();
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'graph');

    expect(pageErrors).toEqual([]);
  });

  test('the list filter is in the list pane and filters only the list that shows; the palette starts the search in the bodies', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1280, height: 800 });
    await waitForClaire(page);

    // The list toggle is after the brand link and the theme picker. The header has no input field.
    const order = await page.evaluate(() => [...document.querySelector('.bar-brand-group').children].map(el => el.id || el.className));
    expect(order.indexOf('listtoggle')).toBeGreaterThan(order.indexOf('theme-picker-wrap'));
    await expect(page.locator('#bar input')).toHaveCount(0);

    // Documents: the filter is below the list switch. "/" in the field is text, not the shortcut.
    const q = page.locator('#docs #q');
    await expect(q).toBeVisible();
    await expect(q).toHaveAttribute('placeholder', /제목·요약/);
    await q.fill('테스트 문서 1');
    await expect(page.locator('#doclist .docitem').first()).toContainText('테스트 문서 1');
    await q.press('/');
    await expect(page.locator('#cmdpalette')).toBeHidden();

    // Entities: a change of the list removes the text, and the field filters entities.
    await page.locator('#listswitch [data-list="entities"]').click();
    await expect(q).toHaveValue('');
    await expect(q).toHaveAttribute('placeholder', /엔티티 이름/);

    // The palette has the command for a search in the document bodies. The result shows in the document list.
    await page.locator('#palettetrigger').click();
    await page.locator('#cmdpalette-input').fill('테스트');
    await page.locator('#cmdpalette-list .cmd-palette-row').filter({ hasText: '본문에서' }).click();
    await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');
    await expect(q).toHaveValue('테스트');

    expect(pageErrors).toEqual([]);
  });

});

test.describe('stage strip (HW-8)', () => {
  // The first page load registers the service worker, and a request that the service worker sends does not go through page.route(). This test replaces responses after the first load, thus it blocks service workers.
  test.use({ serviceWorkers: 'block' });

  test('HW-8: the stage strip shows for the owner with counts from routes that exist and links to /syndication', async ({ page }) => {
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.setViewportSize({ width: 1400, height: 900 });

    // An anonymous session has no strip.
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('anonymous');
    await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
    await expect(page.locator('#home-flow')).toBeHidden();

    // A route that fails gives no count: the stage keeps its link only.
    await page.route('**/api/federation/status', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not_found"}' }));
    // The timeline route works in both modes; here it fails, so the unread count is that of the feeds.
    await page.route('**/api/federation/timeline?limit=1', route => route.fulfill({ status: 404, contentType: 'application/json', body: '{"error":"not_found"}' }));
    await page.route('**/api/federation/following?limit=1', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ total: 3, limit: 1, items: [], next_max_id: null }) }));
    await page.route('**/api/feeds', route => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ feeds: [{ id: 1, unread: 4 }, { id: 2, unread: 1 }], folders: [], limit: 1000 }) }));
    // A login token of the seed database is for one login. This test has its own token.
    await page.goto('/?t=e2e-flow-token-0123456789abcdef012', { waitUntil: 'domcontentloaded' });
    await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
    const flow = page.locator('#home-flow');
    await expect(flow).toBeVisible();
    await expect(flow.locator('a.home-flow-stage')).toHaveCount(3);
    // FP4-5: each stage of the strip goes to its stage of /syndication.
    for (const stage of ['collect', 'ingest', 'publish']) {
      await expect(page.locator('#home-flow-' + stage)).toHaveAttribute('href', '/syndication?stage=' + stage);
    }
    await expect(page.locator('#home-flow-collect-counts')).toHaveText('피드 2 · 안 읽음 5 · 팔로잉 3');
    const documents = await page.evaluate(() => fetch('stats').then(r => r.json()).then(d => d.documents));
    await expect(page.locator('#home-flow-ingest-counts')).toHaveText('문서 ' + documents + ' · 진행 중 0');
    await expect(page.locator('#home-flow-publish-counts')).toHaveText('');
    expect(pageErrors).toEqual([]);
  });
});

test('the home button from the reader view goes to the home view without the graph view between them', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await waitForClaire(page);
  await page.locator('#doclist .docitem').first().click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
  // Record each value of data-center-view from here.
  await page.evaluate(() => {
    window.__views = [];
    new MutationObserver(() => window.__views.push(document.body.dataset.centerView)).observe(document.body, { attributes: true, attributeFilter: ['data-center-view'] });
  });
  await page.locator('#centertab-home').click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'home');
  await expect(page.locator('#homewrap')).toBeVisible();
  await page.waitForTimeout(500);
  const views = await page.evaluate(() => window.__views);
  expect(views).not.toContain('graph');
  expect(views[views.length - 1]).toBe('home');
  expect(pageErrors).toEqual([]);
});

test('WebUI purge menu and command palette dry-run and purge a document for owner without losing session', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/?t=e2e-purgedoc1-token-0123456789abc', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

  // 1. Open doc-1 in reader, then open hamburger menu and check #dedupbtn / #purgebtn.
  await page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 1' }).first().click();
  await expect(page.locator('body')).toHaveAttribute('data-center-view', 'reader');
  await page.locator('#hamburger-btn').click();
  await expect(page.locator('#pop-hamburger-menu')).toBeVisible();
  await expect(page.locator('#dedupbtn')).toBeVisible();
  await expect(page.locator('#purgebtn')).toBeVisible();

  // 2. Click #purgebtn -> #modal-purge opens with #purgetarget pre-filled with doc-1.
  await page.locator('#purgebtn').click();
  const modal = page.locator('dialog#modal-purge');
  await expect(modal).toBeVisible();
  const targetInput = modal.locator('#purgetarget');
  await expect(targetInput).toHaveValue('doc-1');

  // 3. Dry-run with a non-matching target shows an error without expiring owner session.
  await targetInput.fill('no-such-doc-purge-test');
  await modal.locator('.claire-dialog-foot button').filter({ hasText: '영향 분석' }).click();
  await expect(modal.locator('#purgeresult')).toContainText('소각 처리 실패');
  expect(await page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');

  // 4. Dry-run on doc-purge-1 lists the target and shows the confirmation run button.
  await targetInput.fill('doc-purge-1');
  await modal.locator('.claire-dialog-foot button').filter({ hasText: '영향 분석' }).click();
  await expect(modal.locator('#purgeresult')).toContainText('소각 대상 테스트 문서');
  await expect(modal.locator('#purgeresult')).toContainText('doc-purge-1');

  // 5. Accept confirmation dialog, execute purge, verify completion and removal from backend.
  page.once('dialog', dialog => dialog.accept());
  await modal.locator('#purgeresult button').filter({ hasText: '소각 실행' }).click();
  await expect(modal.locator('#purgeresult')).toContainText('영구 소각 완료');
  const docsResp = await page.evaluate(() => fetch('documents?id=doc-purge-1').then(r => r.json()));
  expect(docsResp.documents).toEqual([]);

  // 6. Close modal and open purge again via the "/" command palette.
  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden();
  await page.keyboard.press('/');
  const palette = page.locator('#cmdpalette');
  await expect(palette).toBeVisible();
  await page.locator('#cmdpalette-input').fill('소각');
  const purgeCmd = page.locator('#cmdpalette-list .cmd-palette-row').filter({ hasText: '문서 소각' }).first();
  await expect(purgeCmd).toBeVisible();
  await purgeCmd.click();
  await expect(palette).toBeHidden();
  await expect(modal).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(modal).toBeHidden();

  expect(pageErrors).toEqual([]);
});

test('document list defaults to titles only, places hide below favorite within two-line height, and opens document text from context source rows', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/?t=e2e-doclisthide1-token-0123456789abc', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await expect.poll(() => page.evaluate(() => window.claireDebug?.stabilized)).toBe(true);

  await page.locator('#centertab-reader').click();
  await expect(page.locator('#docs')).toHaveAttribute('data-list', 'docs');

  // 1. Fresh session defaults to titles only (lc0).
  await expect(page.locator('#docs')).toHaveClass(/\blc0\b/);
  await expect(page.locator('#desclines')).toHaveValue('0');
  const firstWithSummary = page.locator('#doclist .docitem').filter({ has: page.locator('p') }).first();
  await expect(firstWithSummary.locator('p')).toBeHidden();
  await page.locator('#desclines').selectOption('3');
  await expect(page.locator('#docs')).toHaveClass(/\blc3\b/);
  await expect(firstWithSummary.locator('p')).toBeVisible();
  await page.locator('#desclines').selectOption('0');
  await expect(page.locator('#docs')).toHaveClass(/\blc0\b/);
  await expect(firstWithSummary.locator('p')).toBeHidden();

  // 2. Hide button sits directly below favorite button inside .docitem-rail within the two-line row height budget.
  const firstDoc = page.locator('#doclist .docitem').first();
  const pinBox = await firstDoc.locator('.docpin-btn').boundingBox();
  const hideBox = await firstDoc.locator('.dochide-btn').boundingBox();
  expect(pinBox).not.toBeNull();
  expect(hideBox).not.toBeNull();
  expect(hideBox.y).toBeGreaterThanOrEqual(pinBox.y + pinBox.height - 0.5);
  expect(Math.abs(hideBox.x - pinBox.x)).toBeLessThanOrEqual(2);

  const desktopMetrics = await page.locator('#doclist .docitem').evaluateAll(rows => rows.slice(0, 10).map(row => {
    const rail = row.querySelector('.docitem-rail');
    return { rowHeight: row.getBoundingClientRect().height, railHeight: rail ? rail.getBoundingClientRect().height : 0 };
  }));
  for (const m of desktopMetrics) {
    expect(m.rowHeight).toBeLessThanOrEqual(55);
    expect(m.railHeight).toBeLessThanOrEqual(37);
  }

  // Mobile viewport also preserves the two-line row height budget.
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => openDocDrawer());
  await expect(page.locator('#doclist .docitem').first()).toBeVisible();
  const mobileMetrics = await page.locator('#doclist .docitem').evaluateAll(rows => rows.slice(0, 10).map(row => {
    const rail = row.querySelector('.docitem-rail');
    return { rowHeight: row.getBoundingClientRect().height, railHeight: rail ? rail.getBoundingClientRect().height : 0 };
  }));
  for (const m of mobileMetrics) {
    expect(m.rowHeight).toBeLessThanOrEqual(64);
    expect(m.railHeight).toBeLessThanOrEqual(42);
  }
  await page.evaluate(() => closeDocDrawer(false));
  await page.setViewportSize({ width: 1280, height: 800 });

  // 3. Clicking the list hide button hides the document and syncs the open Context panel checkbox; unhiding needs no confirm.
  const targetDoc = page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 2' }).first();
  await targetDoc.click();
  await expect(page.locator('#panelhidechk')).not.toBeChecked();

  page.once('dialog', dialog => dialog.accept());
  await targetDoc.locator('.dochide-btn').click();
  await expect(page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 2' })).toHaveCount(0);
  await expect(page.locator('#panelhidechk')).toBeChecked();

  await page.locator('#showhidden').click();
  const hiddenTarget = page.locator('#hiddenlist .docitem').filter({ hasText: '테스트 문서 2' }).first();
  await expect(hiddenTarget).toBeVisible();
  let unhideDialogCount = 0;
  const onDialog = dialog => { unhideDialogCount += 1; dialog.dismiss(); };
  page.on('dialog', onDialog);
  await hiddenTarget.locator('.dochide-btn').click();
  page.off('dialog', onDialog);
  expect(unhideDialogCount).toBe(0);
  await expect(page.locator('#doclist .docitem').filter({ hasText: '테스트 문서 2' })).toHaveCount(1);
  await expect(page.locator('#panelhidechk')).not.toBeChecked();

  // 4. Context panel source documents show 본문 열기 (not 원문 열기) and open the document in the reader.
  await page.evaluate(() => selectEntity('ent-list-01'));
  const firstSrc = page.locator('#panel .srcrow').first();
  await firstSrc.locator('.srctitle').click();
  await expect(firstSrc.locator('.srcbody .srcopen')).toHaveText('본문 열기');
  await expect(firstSrc.locator('.srcbody')).not.toContainText('원문 열기');
  await firstSrc.locator('.srcbody .srcopen').click();
  await expect(page.locator('#reader')).toContainText('페이징 자료 01의 본문입니다.');

  expect(pageErrors).toEqual([]);
});
