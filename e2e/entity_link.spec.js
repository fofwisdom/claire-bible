const fs = require('fs');
const path = require('path');
const { test, expect } = require('@playwright/test');

// The page of an entity for a caller who has only link access (PERMALINK_UNIFICATION_DESIGN.md Section 6.2 rule 3).
// The E2E server has one public knowledge base, thus the route never gives this page there. The spec fills the real template with data in the form that "_entity_link_page" makes, and the scripts of the graph engine come from the E2E server.
function entityLinkPage() {
  const template = fs.readFileSync(path.join(__dirname, '..', 'src', 'claire', 'api', 'templates', 'entity_link.html'), 'utf8');
  const data = {
    nodes: [
      { id: 'e0', label: 'vSAN', group: 'Tool', degree: 3 },
      { id: 'e1', label: 'ESX', group: 'Tool', degree: 1 },
      { id: 'e2', label: 'VMware Cloud Foundation', group: 'Tool', degree: 1 },
      { id: 'e3', label: 'Write Buffer', group: 'Concept', degree: 1 },
    ],
    edges: [
      { id: 'r0', from: 'e0', to: 'e1', label: 'uses', arrows: 'to', dashes: false },
      { id: 'r1', from: 'e2', to: 'e0', label: 'uses', arrows: 'to', dashes: false },
      { id: 'r2', from: 'e0', to: 'e3', label: 'uses', arrows: 'to', dashes: false },
    ],
  };
  const values = {
    lang: 'ko', title: 'vSAN · Claire Bible', canonical: '/entity/45678923abcdefgh', graph_label: 'vSAN와(과) 직접 관계의 그래프',
    panel: '<h1>vSAN<small>Tool</small></h1><h2>관계 (3)</h2><ul id="entity-relations"><li>→ uses ESX</li></ul>', note: '이 페이지는 엔티티 하나와 직접 관계만 보여 줍니다.',
    data: JSON.stringify(data),
  };
  return template.replace(/\$([a-z_]+)/g, (whole, name) => (name in values ? values[name] : whole));
}

test('the link access page of an entity draws the entity and its direct relations with the graph engine', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1300, height: 800 });
  await page.route('**/entity/45678923abcdefgh', route => route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: entityLinkPage() }));
  await page.goto('/entity/45678923abcdefgh', { waitUntil: 'load' });
  await expect(page.locator('#entity-panel h1')).toContainText('vSAN');
  await expect(page.locator('#entity-relations li')).toHaveCount(1);
  // The graph engine made the network with the four entities, and it drew on a canvas.
  await expect.poll(() => page.evaluate(() => Boolean(window.claireEntityNet))).toBe(true);
  await expect(page.locator('#entity-net canvas').first()).toBeVisible();
  const drawn = await page.evaluate(() => {
    const canvas = document.querySelector('#entity-net canvas');
    return canvas.width > 100 && canvas.height > 100;
  });
  expect(drawn).toBe(true);
  expect(pageErrors).toEqual([]);
});
