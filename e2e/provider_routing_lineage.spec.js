const { test, expect } = require('@playwright/test');

test.use({ serviceWorkers: 'block' });

test('admin providers visualizes pipeline lineage at top, consolidates server_tier, and saves routing without embed_task_type dropdown', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/?t=e2e-routing-token-0123456789abcde', { waitUntil: 'domcontentloaded' });
  await expect.poll(() => page.evaluate(() => window.claireDebug?.authScope)).toBe('owner');
  await expect.poll(() => page.evaluate(() => window.claireDebug?.stabilized)).toBe(true);
  await page.evaluate(async () => {
    if (!navigator.serviceWorker) return;
    const regs = await navigator.serviceWorker.getRegistrations();
    await Promise.all(regs.map(r => r.unregister()));
  });

  let patchedPayload = null;
  await page.route(u => u.pathname === '/providers', async (route) => {
    const req = route.request();
    if (req.method() === 'GET') {
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          active_provider: 'gemini',
          effective_provider: 'gemini',
          effective_stt_provider: 'mock',
          server_tier: { provider: 'gemini', model: 'gemini-3.8-flash', max_effort: 'medium', features: ['expand', 'stt'] },
          server_tier_configured: true,
          feature_routing: {
            extract_graph: { provider: 'inherit', model: '', effort: '' },
            summarize: { provider: 'gemini', model: 'gemini-4-argon', effort: 'high' },
            render_detail: { provider: 'inherit', model: '', effort: '' },
            entity_judge: { provider: 'inherit', model: '', effort: '' },
            relation_judge: { provider: 'inherit', model: '', effort: '' },
            classify: { provider: 'inherit', model: '', effort: '' },
            research: { provider: 'inherit', model: '', effort: '' },
            embed: { provider: 'gemini', model: 'gemini-embedding-001', effort: '' },
            stt: { provider: 'inherit', model: '', effort: '' },
            vlm: { provider: 'inherit', model: '', effort: '' }
          },
          providers: {
            gemini: {
              has_api_key: true,
              api_key: '••••••••',
              model: 'gemini-3.8-flash',
              effort: 'medium',
              embed_model: 'gemini-embedding-001',
              available_models: [
                { id: 'gemini-3.5-flash-lite-medium' },
                { id: 'gemini-3.8-flash-low' },
                { id: 'gemini-3.8-flash-medium' },
                { id: 'gemini-3.8-flash-high' },
                { id: 'gemini-4-argon-medium' },
                { id: 'gemini-4-argon-high' }
              ],
              available_embed_models: [{ id: 'gemini-embedding-001' }]
            },
            jev: { enabled: true, has_api_key: true, api_key: '••••••••', base_url: 'https://api.typesafe.ai/v1' },
            stt: { enabled: false, provider: 'mock', language: 'ko' }
          },
          lineage_summary: {
            effective_slots: {
              extract_graph: { provider: 'gemini', model: 'gemini-3.8-flash', effort: 'medium' },
              summarize: { provider: 'gemini', model: 'gemini-4-argon', effort: 'high' },
              embed: { provider: 'gemini', model: 'gemini-embedding-001', effort: '' },
              entity_judge: { provider: 'gemini', model: 'gemini-3.8-flash', effort: 'medium' },
              relation_judge: { provider: 'gemini', model: 'gemini-3.8-flash', effort: 'medium' }
            },
            jev: { enabled: true, role: 'shadow', shadow_pairs: 249, ok_calls: 249, avg_latency_ms: 224.6 },
            architecture_specs: [
              { stage: '임베딩 Task Type (문서 색인)', fixed_value: 'RETRIEVAL_DOCUMENT', scope: 'extract/gemini_provider.py', rationale: '색인 고정' },
              { stage: '임베딩 Task Type (검색 질의)', fixed_value: 'RETRIEVAL_QUERY', scope: 'retrieval/query.py', rationale: '질의 고정' }
            ],
            fallbacks: [
              { id: 'F2', location: 'gemini_provider.extract', status: 'REMOVED', description: 'Removed silent plain-text fallback.' },
              { id: 'F10', location: 'pdf_fetcher', status: 'OUT_OF_SCOPE', description: 'Non-LLM parser chain left unchanged.' }
            ],
            recent_documents: [
              { id: 'doc1', title: 'Sample Doc', lineage: { final_output_models: { extract_graph: { provider: 'gemini', model: 'gemini-3.8-flash' }, summarize: { provider: 'gemini', model: 'gemini-4-argon' } }, jev: { enabled: true, role: 'shadow', evaluated: 3 }, fallbacks_triggered: [] } }
            ]
          },
          capabilities: {
            active_provider: 'gemini',
            can_save: true,
            lock_reasons: [],
            warnings: [],
            stt: { enabled: false, provider: 'mock', ready: true },
            providers: {
              gemini: { selectable: true, locked_reason: '', features: { extract: true, research: true, embedding: true, stt_vlm: true } },
              mock: { selectable: true, locked_reason: '', features: { extract: false, research: false, embedding: false, stt_vlm: false } }
            }
          }
        })
      });
    }
    if (req.method() === 'PATCH') {
      patchedPayload = JSON.parse(req.postData() || '{}');
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          config: {
            active_provider: patchedPayload.active_provider,
            effective_provider: patchedPayload.active_provider,
            effective_stt_provider: 'mock',
            server_tier: patchedPayload.server_tier || { provider: 'gemini', model: 'gemini-3.8-flash', max_effort: 'medium', features: ['expand', 'stt'] },
            feature_routing: patchedPayload.feature_routing,
            providers: {
              gemini: { has_api_key: true, ...patchedPayload.providers.gemini, available_models: [{ id: 'gemini-3.5-flash-lite-medium' }, { id: 'gemini-3.8-flash-low' }, { id: 'gemini-3.8-flash-medium' }, { id: 'gemini-3.8-flash-high' }, { id: 'gemini-4-argon-medium' }, { id: 'gemini-4-argon-high' }], available_embed_models: [{ id: 'gemini-embedding-001' }] },
              jev: { enabled: true, has_api_key: true, ...patchedPayload.providers.jev },
              stt: patchedPayload.providers.stt
            },
            lineage_summary: {
              effective_slots: { extract_graph: { provider: 'gemini', model: 'gemini-3.8-flash', effort: 'medium' }, summarize: { provider: 'gemini', model: 'gemini-4-argon', effort: 'high' }, embed: { provider: 'gemini', model: 'gemini-embedding-001', effort: '' } },
              jev: { enabled: true, role: 'shadow', shadow_pairs: 249, ok_calls: 249, avg_latency_ms: 224.6 },
              architecture_specs: [
                { stage: '임베딩 Task Type (문서 색인)', fixed_value: 'RETRIEVAL_DOCUMENT', scope: 'extract/gemini_provider.py', rationale: '색인 고정' }
              ],
              fallbacks: [],
              recent_documents: []
            },
            capabilities: { active_provider: 'gemini', can_save: true, lock_reasons: [], warnings: [], stt: { enabled: false, provider: 'mock', ready: true }, providers: { gemini: { selectable: true, features: { extract: true, research: true, embedding: true, stt_vlm: true } }, mock: { selectable: true, features: {} } } }
          }
        })
      });
    }
    return route.continue();
  });

  await page.route(u => u.pathname === '/providers/check', async (route) => {
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        active_provider: 'gemini',
        can_save: true,
        lock_reasons: [],
        warnings: [],
        stt: { enabled: false, provider: 'mock', ready: true },
        providers: {
          gemini: { selectable: true, locked_reason: '', features: { extract: true, research: true, embedding: true, stt_vlm: true } },
          mock: { selectable: true, locked_reason: '', features: {} }
        }
      })
    });
  });

  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#prov-lineage-visualizer-card')).toBeVisible();
  await expect(page.locator('#prov-feature-routing-card')).toBeVisible();
  await expect(page.locator('#prov-member-tier-card')).toBeVisible();
  await expect(page.locator('#prov-gemini-embed')).toHaveCount(0);
  await expect(page.locator('#prov-gemini-effort')).toHaveCount(0);
  await expect(page.locator('#prov-tier-max-effort')).toHaveCount(0);
  await expect(page.locator('#prov-route-extract_graph-effort')).toHaveCount(0);
  await expect(page.locator('#prov-route-embed-provider')).toHaveCount(0);
  await expect(page.locator('#prov-route-embed-model')).toHaveCount(0);
  await expect(page.locator('#prov-gemini-model')).toHaveValue('gemini-3.8-flash-medium');
  await expect(page.locator('#prov-route-summarize-model')).toHaveValue('gemini-4-argon-high');
  await expect(page.locator('#prov-route-render_detail-provider')).toBeVisible();
  await expect(page.locator('#prov-route-render_detail-model')).toBeVisible();
  await page.locator('#prov-route-render_detail-provider').selectOption('gemini');
  await page.locator('#prov-route-render_detail-model').selectOption('gemini-3.8-flash-high');
  await expect(page.locator('#prov-tier-model')).toHaveValue('gemini-3.8-flash-medium');
  await expect(page.locator('#prov-gemini-embed-info')).toContainText('gemini-embedding-001');
  await expect(page.locator('#prov-routing-embed-note')).toContainText('gemini-embedding-001');
  await expect(page.locator('#prov-gemini-embed-task-type')).toHaveCount(0);

  const linTop = await page.locator('#prov-lineage-visualizer-card').evaluate(el => el.getBoundingClientRect().top);
  const featTop = await page.locator('#prov-feature-routing-card').evaluate(el => el.getBoundingClientRect().top);
  expect(linTop).toBeLessThan(featTop);

  await expect(page.locator('#prov-lineage-svg')).toBeVisible();
  await expect(page.locator('#prov-lineage-svg')).toContainText('gemini-4-argon');
  await expect(page.locator('#prov-architecture-spec-table')).toContainText('RETRIEVAL_DOCUMENT');
  await expect(page.locator('#prov-recent-lineage-table')).toContainText('Sample Doc');

  await page.locator('#prov-gemini-model').selectOption('gemini-3.8-flash-high');
  // Jev has only the shadow role: no mode control, a fixed note, and the lineage shows shadow (owner decision 2026-11-20).
  await expect(page.locator('#prov-jev-mode')).toHaveCount(0);
  await expect(page.locator('#prov-jev-filter-threshold')).toHaveCount(0);
  await expect(page.locator('#prov-jev-role')).toContainText('shadow');
  await expect(page.locator('#prov-lineage-svg')).toContainText('jev:shadow');
  await expect(page.locator('#prov-recent-lineage-table')).toContainText('shadow (3)');
  await page.locator('#prov-save-btn').click();

  await expect(page.getByText('프로바이더 설정이 성공적으로 저장되었습니다')).toBeVisible();
  expect(patchedPayload).not.toBeNull();
  expect(patchedPayload.providers.gemini.model).toBe('gemini-3.8-flash-high');
  expect(patchedPayload.providers.gemini.effort).toBe('high');
  expect(patchedPayload.providers.jev).toEqual({ enabled: true, base_url: 'https://api.typesafe.ai/v1' });
  expect(patchedPayload.feature_routing.summarize.model).toBe('gemini-4-argon-high');
  expect(patchedPayload.feature_routing.summarize.effort).toBe('high');
  expect(patchedPayload.feature_routing.render_detail.provider).toBe('gemini');
  expect(patchedPayload.feature_routing.render_detail.model).toBe('gemini-3.8-flash-high');
  expect(patchedPayload.feature_routing.render_detail.effort).toBe('high');
  expect(patchedPayload.server_tier.provider).toBe('gemini');
  expect(patchedPayload.server_tier.model).toBe('gemini-3.8-flash-medium');
  expect(patchedPayload.server_tier.max_effort).toBe('medium');
  expect(patchedPayload.server_tier.features).toEqual(['expand', 'stt']);

  // The personal CLI clients of the administrator are removed: the keys page shows only the link to the provider page and asks for no personal CLI state.
  const personalCliRequests = [];
  page.on('request', req => { if (new URL(req.url()).pathname.startsWith('/api/me/personal-cli')) personalCliRequests.push(req.url()); });
  await page.goto('/settings/keys', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#settings-keys-admin-providers')).toBeVisible();
  await expect(page.locator('#settings-keys-admin-providers a[href="/admin/providers"]')).toBeVisible();
  await expect(page.locator('#admin-personal-cli-host')).toHaveCount(0);
  await expect(page.locator('[data-personal-cli-form]')).toHaveCount(0);
  await expect(page.locator('#settings-keys-admin-providers')).not.toContainText('CLI');
  expect(personalCliRequests).toEqual([]);
  expect(pageErrors).toEqual([]);
});
