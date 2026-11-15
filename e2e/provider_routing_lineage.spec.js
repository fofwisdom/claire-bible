const { test, expect } = require('@playwright/test');

test.use({ serviceWorkers: 'block' });

test('admin providers configures feature routing, jev mode, embed task type, and visualizes pipeline lineage', async ({ page }) => {
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto('/?t=e2e-owner-token-0123456789abcdef', { waitUntil: 'domcontentloaded' });
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
          embed_task_types: ['SEMANTIC_SIMILARITY', 'RETRIEVAL_DOCUMENT', 'RETRIEVAL_QUERY', 'CLASSIFICATION', 'CLUSTERING'],
          feature_routing: {
            extract_graph: { provider: 'inherit', model: '', effort: '' },
            summarize: { provider: 'gemini', model: 'gemini-4-argon', effort: 'high' },
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
              embed_task_type: 'RETRIEVAL_DOCUMENT',
              available_models: [{ id: 'gemini-3.8-flash' }, { id: 'gemini-4-argon' }],
              available_embed_models: [{ id: 'gemini-embedding-001' }]
            },
            antigravity: { installed: false, bin: 'agy', model: '', available_models: [] },
            codex: { installed: false, bin: 'codex', model: '', available_models: [{ id: 'gpt-5.4' }] },
            openai: { has_api_key: false, api_key: '', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', embed_model: 'text-embedding-3-small' },
            jev: { enabled: true, has_api_key: true, api_key: '••••••••', base_url: 'https://api.typesafe.ai/v1', mode: 'filter', filter_threshold: 0.2, auto_merge_threshold: 0.9 },
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
            jev: { enabled: true, mode: 'filter', filter_threshold: 0.2, auto_merge_threshold: 0.9, shadow_pairs: 249, ok_calls: 249, avg_latency_ms: 224.6, below_filter_threshold: 180 },
            fallbacks: [
              { id: 'F1', location: 'antigravity_provider.py', status: 'REMOVED', description: 'Removed silent plain-text fallback.' },
              { id: 'F10', location: 'pdf_fetcher', status: 'OUT_OF_SCOPE', description: 'Non-LLM parser chain left unchanged.' }
            ],
            recent_documents: [
              { id: 'doc1', title: 'Sample Doc', lineage: { final_output_models: { extract_graph: { provider: 'gemini', model: 'gemini-3.8-flash' }, summarize: { provider: 'gemini', model: 'gemini-4-argon' } }, jev: { enabled: true, mode: 'filter', filtered_count: 2, hybrid_auto_merged_count: 0 }, fallbacks_triggered: [] } }
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
              antigravity: { selectable: false, locked_reason: 'not installed', available_models: [], features: { extract: true, research: true, embedding: false, stt_vlm: false } },
              codex: { selectable: false, locked_reason: 'not installed', features: { extract: true, research: true, embedding: false, stt_vlm: false } },
              openai: { selectable: false, locked_reason: 'not implemented', features: { extract: false, research: false, embedding: false, stt_vlm: false } },
              mock: { selectable: true, locked_reason: '', features: { extract: true, research: true, embedding: true, stt_vlm: true } }
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
            feature_routing: patchedPayload.feature_routing,
            providers: {
              gemini: { has_api_key: true, ...patchedPayload.providers.gemini, available_models: [{ id: 'gemini-3.8-flash' }, { id: 'gemini-4-argon' }], available_embed_models: [{ id: 'gemini-embedding-001' }] },
              antigravity: { installed: false, bin: 'agy', model: '', available_models: [] },
              codex: { installed: false, bin: 'codex', model: '', available_models: [{ id: 'gpt-5.4' }] },
              openai: { has_api_key: false, base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini', embed_model: 'text-embedding-3-small' },
              jev: { enabled: true, has_api_key: true, ...patchedPayload.providers.jev },
              stt: patchedPayload.providers.stt
            },
            lineage_summary: {
              effective_slots: { extract_graph: { provider: 'gemini', model: 'gemini-3.8-flash', effort: 'medium' }, summarize: { provider: 'gemini', model: 'gemini-4-argon', effort: 'high' }, embed: { provider: 'gemini', model: 'gemini-embedding-001', effort: '' } },
              jev: { enabled: true, mode: patchedPayload.providers.jev.mode, filter_threshold: patchedPayload.providers.jev.filter_threshold, auto_merge_threshold: patchedPayload.providers.jev.auto_merge_threshold, shadow_pairs: 249, ok_calls: 249, avg_latency_ms: 224.6, below_filter_threshold: 180 },
              fallbacks: [],
              recent_documents: []
            },
            capabilities: { active_provider: 'gemini', can_save: true, lock_reasons: [], warnings: [], stt: { enabled: false, provider: 'mock', ready: true }, providers: { gemini: { selectable: true, features: { extract: true, research: true, embedding: true, stt_vlm: true } }, antigravity: { selectable: false, locked_reason: 'not installed', features: {} }, codex: { selectable: false, locked_reason: 'not installed', features: {} }, openai: { selectable: false, locked_reason: 'not implemented', features: {} }, mock: { selectable: true, features: {} } } }
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
          antigravity: { selectable: false, locked_reason: 'not installed', available_models: [], features: {} },
          codex: { selectable: false, locked_reason: 'not installed', features: {} },
          openai: { selectable: false, locked_reason: 'not implemented', features: {} },
          mock: { selectable: true, locked_reason: '', features: {} }
        }
      })
    });
  });

  await page.goto('/admin/providers', { waitUntil: 'domcontentloaded' });
  await expect(page.locator('#prov-feature-routing-card')).toBeVisible();
  await expect(page.locator('#prov-lineage-svg')).toBeVisible();
  await expect(page.locator('#prov-lineage-svg')).toContainText('gemini-4-argon');
  await expect(page.locator('#prov-recent-lineage-table')).toContainText('Sample Doc');

  await page.locator('#prov-jev-mode').selectOption('hybrid');
  await expect(page.locator('#prov-lineage-svg')).toContainText('jev:hybrid');
  await page.locator('#prov-save-btn').click();

  await expect(page.getByText('프로바이더 설정이 성공적으로 저장되었습니다')).toBeVisible();
  expect(patchedPayload).not.toBeNull();
  expect(patchedPayload.providers.gemini.embed_task_type).toBe('RETRIEVAL_DOCUMENT');
  expect(patchedPayload.providers.jev.mode).toBe('hybrid');
  expect(patchedPayload.feature_routing.summarize.model).toBe('gemini-4-argon');
  expect(pageErrors).toEqual([]);
});
