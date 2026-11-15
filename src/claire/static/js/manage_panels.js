/**
 * Claire Shared Management Panels Controller (manage_panels.js)
 * Reusable DOM and API controllers for Provider, Limit, Theme, and Token management.
 * Loaded by index.html and console_shell.html.
 */
(function() {
  'use strict';

  function esc(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function jsArg(v) {
    return esc(JSON.stringify(String(v ?? '')));
  }

  // Catalog text (static/js/i18n.js). Without the runtime, the key shows.
  function trKey(key, params) {
    return window.ClaireI18n ? window.ClaireI18n.t(key, params) : key;
  }

  if (typeof window.esc !== 'function') window.esc = esc;
  if (typeof window.jsArg !== 'function') window.jsArg = jsArg;

  function resolveContainer(c) {
    if (typeof c === 'string') return document.querySelector(c);
    return c;
  }

  function resolveRootUrl(opts, path) {
    if (opts && typeof opts.rootUrl === 'function') return opts.rootUrl(path);
    if (opts && typeof opts.rootUrl === 'string') return opts.rootUrl + (path.startsWith('/') ? path.slice(1) : path);
    if (typeof window.rootUrl === 'function') return window.rootUrl(path);
    const p = (path || '').startsWith('/') ? path.slice(1) : (path || '');
    return '/' + p;
  }

  function resolveApiUrl(opts, path) {
    if (opts && typeof opts.apiUrl === 'function') return opts.apiUrl(path);
    if (opts && typeof opts.apiUrl === 'string') return opts.apiUrl + (path.startsWith('/') ? path.slice(1) : path);
    if (typeof window.apiUrl === 'function') return window.apiUrl(path);
    const p = (path || '').startsWith('/') ? path.slice(1) : (path || '');
    return '/api/' + p;
  }

  function evalBool(val, defaultVal = true) {
    if (typeof val === 'function') return Boolean(val());
    if (typeof val === 'boolean') return val;
    return defaultVal;
  }

  function getThemeKey(t) {
    if (!t) return '0';
    if (t.is_default || String(t.id) === '0' || t.number === '0') return '0';
    if (t.number !== null && t.number !== undefined && String(t.number).trim() !== '') {
      return String(t.number).trim();
    }
    return String(t.id ?? '0').trim();
  }

  const _provState = { container: null, opts: {}, config: null };
  const _limitState = { container: null, opts: {}, config: null };
  const _themeState = { container: null, opts: {}, themes: [] };
  const _tokenState = { container: null, opts: {}, tokens: [], newCreatedToken: null };

  function findTheme(themeRef) {
    const themes = _themeState.themes || (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.themes) || [];
    const ref = String(themeRef ?? '').trim();
    if (!ref || ref === '0') {
      return themes.find(t => t.is_default || String(t.id) === '0' || t.number === '0') || themes[0] || null;
    }
    return themes.find(t => String(t.number) === ref) || themes.find(t => String(t.id) === ref) || null;
  }

  // ==========================================
  // 1. PROVIDER MANAGER
  // ==========================================

  function togglePassVisibility(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.type = el.type === 'password' ? 'text' : 'password';
  }

  function clearApiKeyInput(inputId, provName) {
    const el = document.getElementById(inputId);
    if (el) {
      el.value = '';
      el.placeholder = 'API Key 입력 (비어있음)';
      el.dataset.cleared = 'true';
    }
    runProviderCapabilityCheck();
  }

  function closeProviderManager() {
    if (_provState.opts && typeof _provState.opts.onClose === 'function') {
      _provState.opts.onClose();
    } else if (typeof window.closeDrawer === 'function') {
      window.closeDrawer();
    }
  }

  function normalizeModelOptions(rawList, fallbackList) {
    const src = Array.isArray(rawList) ? (rawList.length ? rawList : []) : (Array.isArray(fallbackList) ? fallbackList : []);
    return src.map(item => (typeof item === 'string' ? { id: item, label: '' } : { id: String(item.id || ''), label: String(item.label || '') }));
  }

  function buildModelSelectResult(rawList, fallbackList, currentVal, defaultId) {
    const items = normalizeModelOptions(rawList, fallbackList);
    const ids = new Set(items.map(m => m.id));
    const cur = currentVal == null ? '' : String(currentVal);
    if (items.length === 0) {
      let optionsHtml = '';
      if (cur !== '') {
        optionsHtml += `<option value="${esc(cur)}" selected disabled>${esc(cur)} (미검증 · 실행 불가)</option>`;
      }
      optionsHtml += `<option value="" ${cur === '' ? 'selected ' : ''}disabled>지원 모델 조회 불가 (잠금)</option>`;
      return { optionsHtml, selected: cur, unsupportedCurrent: cur !== '', locked: true, previousVal: cur };
    }
    let selected = cur;
    let unsupportedCurrent = false;
    if (!ids.has(cur)) {
      if (cur !== '') {
        unsupportedCurrent = true;
        selected = cur;
      } else {
        selected = defaultId && ids.has(defaultId) ? defaultId : items[0].id;
      }
    }
    let optionsHtml = '';
    if (unsupportedCurrent) {
      optionsHtml += `<option value="${esc(cur)}" selected>${esc(cur)} (미지원 · 교체 필요)</option>`;
    }
    for (const m of items) {
      const text = m.id === '' ? (m.label || '계정 기본') : m.id;
      optionsHtml += `<option value="${esc(m.id)}" ${selected === m.id ? 'selected' : ''}>${esc(text)}</option>`;
    }
    return { optionsHtml, selected, unsupportedCurrent, locked: false, previousVal: cur };
  }

  function renderModelSelectField(id, rawList, fallbackList, currentVal, defaultId) {
    const res = buildModelSelectResult(rawList, fallbackList, currentVal, defaultId);
    const disabledAttr = res.locked ? ' disabled' : '';
    let html = `<select id="${esc(id)}"${disabledAttr} style="width:100%;box-sizing:border-box;margin-top:2px;">${res.optionsHtml}</select>`;
    if (res.unsupportedCurrent) {
      html += `<div style="margin-top:3px;font-size:10px;color:var(--err,#cf222e);">현재 값(<code>${esc(res.previousVal)}</code>)은 지원 목록에 없어 실행할 수 없습니다. 지원되는 모델을 선택해 주세요.</div>`;
    }
    return html;
  }

  let _capAbortCtrl = null;
  const PROVIDER_LABELS = {
    gemini: 'Google AI Studio (권장 - 고품질 LLM 및 임베딩)',
    mock: 'Mock (가상 테스트 프로바이더 - 외부 호출 없음)'
  };

  function collectProviderFormPayload() {
    const activeProv = document.getElementById('prov-active-select').value;
    const gemKey = document.getElementById('prov-gemini-key');
    const gemModel = document.getElementById('prov-gemini-model').value.trim();
    const gemEffort = document.getElementById('prov-gemini-effort').value;
    const gemEmbed = document.getElementById('prov-gemini-embed').value.trim();
    const gemInterval = parseFloat(document.getElementById('prov-gemini-interval').value) || 4.0;
    const gemRetries = parseInt(document.getElementById('prov-gemini-retries').value, 10) || 5;

    const jevEnabled = document.getElementById('prov-jev-enabled').checked;
    const jevKey = document.getElementById('prov-jev-key');
    const jevUrl = document.getElementById('prov-jev-url').value.trim();
    const jevModeEl = document.getElementById('prov-jev-mode');
    const jevMode = jevModeEl ? jevModeEl.value : 'filter';
    const jevFilterEl = document.getElementById('prov-jev-filter-threshold');
    const jevFilterRaw = jevFilterEl ? parseFloat(jevFilterEl.value) : 0.20;
    const jevFilterThr = Number.isFinite(jevFilterRaw) ? jevFilterRaw : 0.20;
    const jevAutoEl = document.getElementById('prov-jev-auto-merge-threshold');
    const jevAutoRaw = jevAutoEl ? parseFloat(jevAutoEl.value) : 0.90;
    const jevAutoThr = Number.isFinite(jevAutoRaw) ? jevAutoRaw : 0.90;

    const sttEnabled = document.getElementById('prov-stt-enabled').checked;
    const sttProv = document.getElementById('prov-stt-prov').value;
    const sttLang = document.getElementById('prov-stt-lang').value.trim();

    const featureRouting = {};
    const slotNames = ['extract_graph', 'summarize', 'entity_judge', 'relation_judge', 'classify', 'research', 'embed', 'stt', 'vlm'];
    for (const slot of slotNames) {
      const pEl = document.getElementById(`prov-route-${slot}-provider`);
      const mEl = document.getElementById(`prov-route-${slot}-model`);
      const eEl = document.getElementById(`prov-route-${slot}-effort`);
      if (pEl || mEl || eEl) {
        featureRouting[slot] = {
          provider: pEl ? String(pEl.value || 'inherit') : 'inherit',
          model: mEl ? String(mEl.value || '').trim() : '',
          effort: (eEl && slot !== 'embed') ? String(eEl.value || '').trim().toLowerCase() : ''
        };
      }
    }

    const payload = {
      active_provider: activeProv,
      feature_routing: featureRouting,
      providers: {
        gemini: { model: gemModel, effort: gemEffort, embed_model: gemEmbed, min_interval: gemInterval, max_retries: gemRetries },
        jev: { enabled: jevEnabled, base_url: jevUrl, mode: jevMode, filter_threshold: jevFilterThr, auto_merge_threshold: jevAutoThr },
        stt: { enabled: sttEnabled, provider: sttProv, language: sttLang }
      }
    };

    const tierProvEl = document.getElementById('prov-tier-provider');
    if (tierProvEl) {
      const tierModelEl = document.getElementById('prov-tier-model');
      const tierEffortEl = document.getElementById('prov-tier-max-effort');
      const tierFeats = Array.from(document.querySelectorAll('input[name="prov-tier-feature"]:checked')).map((el) => el.value);
      payload.server_tier = {
        provider: tierProvEl.value || 'none',
        model: tierProvEl.value === 'none' ? '' : ((tierModelEl && tierModelEl.value.trim()) || ''),
        max_effort: (tierEffortEl && tierEffortEl.value) || 'medium',
        features: tierFeats
      };
    }

    if (gemKey && gemKey.value) payload.providers.gemini.api_key = gemKey.value;
    else if (gemKey && gemKey.dataset.cleared === 'true') payload.providers.gemini.clear_api_key = true;

    if (jevKey && jevKey.value) payload.providers.jev.api_key = jevKey.value;
    else if (jevKey && jevKey.dataset.cleared === 'true') payload.providers.jev.clear_api_key = true;

    return payload;
  }

  function renderFeatureBadgesHtml(cap) {
    if (!cap || !cap.features) return '';
    const f = cap.features;
    const chip = (label, ok, note) => {
      const color = ok ? 'var(--accent2,#1a7f37)' : 'var(--muted,#6e7781)';
      const bg = ok ? 'rgba(26,127,55,0.1)' : 'rgba(136,136,136,0.1)';
      return `<span style="display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;background:${bg};color:${color};margin-right:4px;margin-bottom:3px;" title="${esc(note || '')}">${ok ? '지원' : '미지원'} · ${esc(label)}</span>`;
    };
    let out = '<div style="margin:4px 0 6px;">';
    out += chip('적재·요약', Boolean(f.extract));
    out += chip('웹 리서치', Boolean(f.research));
    out += chip('벡터 임베딩', Boolean(f.embedding), cap.embedding_note || '');
    out += chip('STT·VLM', Boolean(f.stt_vlm));
    out += '</div>';
    return out;
  }

  function applyProviderCapabilities(caps) {
    if (!caps || !caps.providers) return;
    _provState.capabilities = caps;
    const activeSelect = document.getElementById('prov-active-select');
    if (activeSelect) {
      const currentVal = activeSelect.value;
      for (const opt of Array.from(activeSelect.options)) {
        const cap = caps.providers[opt.value];
        const baseLabel = PROVIDER_LABELS[opt.value] || opt.value;
        if (cap && cap.selectable === false) {
          opt.disabled = opt.value !== currentVal;
          opt.textContent = `${baseLabel} [사용 불가 · 잠금]`;
        } else {
          opt.disabled = false;
          opt.textContent = baseLabel;
        }
      }
    }

    for (const [name, cap] of Object.entries(caps.providers)) {
      const featEl = document.getElementById('prov-feat-' + name);
      if (featEl) featEl.innerHTML = renderFeatureBadgesHtml(cap);
      const lockEl = document.getElementById('prov-cap-' + name);
      if (lockEl) {
        if (cap.locked_reason) {
          lockEl.style.display = 'block';
          lockEl.style.color = 'var(--err,#cf222e)';
          lockEl.style.background = 'rgba(207,34,46,0.08)';
          lockEl.style.borderColor = 'rgba(207,34,46,0.35)';
          lockEl.textContent = '잠금: ' + cap.locked_reason;
        } else if (cap.embedding_note) {
          lockEl.style.display = 'block';
          lockEl.style.color = 'var(--warn,#9a6700)';
          lockEl.style.background = 'rgba(154,103,0,0.08)';
          lockEl.style.borderColor = 'rgba(154,103,0,0.35)';
          lockEl.textContent = '기능 안내: ' + cap.embedding_note;
        } else if (cap.advisory) {
          lockEl.style.display = 'block';
          lockEl.style.color = 'var(--warn,#9a6700)';
          lockEl.style.background = 'rgba(154,103,0,0.08)';
          lockEl.style.borderColor = 'rgba(154,103,0,0.35)';
          lockEl.textContent = cap.advisory;
        } else {
          lockEl.style.display = 'none';
          lockEl.textContent = '';
        }
      }
    }

    const gemCap = caps.providers.gemini;
    if (gemCap && Array.isArray(gemCap.available_models) && gemCap.available_models.length) {
      if (_provState.config && _provState.config.providers && _provState.config.providers.gemini) {
        _provState.config.providers.gemini.available_models = gemCap.available_models;
        if (Array.isArray(gemCap.available_embed_models) && gemCap.available_embed_models.length) {
          _provState.config.providers.gemini.available_embed_models = gemCap.available_embed_models;
        }
      }
      const gemSel = document.getElementById('prov-gemini-model');
      if (gemSel) {
        const resG = buildModelSelectResult(gemCap.available_models, null, gemSel.value, 'gemini-3.8-flash');
        gemSel.innerHTML = resG.optionsHtml;
        gemSel.value = resG.selected;
      }
      const tierModelSel = document.getElementById('prov-tier-model');
      if (tierModelSel && tierModelSel.tagName === 'SELECT') {
        const resT = buildModelSelectResult(gemCap.available_models, null, tierModelSel.value, 'gemini-3.5-flash-lite');
        tierModelSel.innerHTML = '<option value="">선택 안 함 (ServerTier provider=none)</option>' + resT.optionsHtml;
        if (tierModelSel.querySelector(`option[value="${CSS.escape(resT.selected)}"]`)) {
          tierModelSel.value = resT.selected;
        }
      }
    }
    const gemEmbSel = document.getElementById('prov-gemini-embed');
    if (gemEmbSel && gemCap && Array.isArray(gemCap.available_embed_models) && gemCap.available_embed_models.length) {
      const resE = buildModelSelectResult(gemCap.available_embed_models, null, gemEmbSel.value, 'gemini-embedding-001');
      gemEmbSel.innerHTML = resE.optionsHtml;
      gemEmbSel.value = resE.selected;
    }

    const sttEl = document.getElementById('prov-cap-stt');
    if (sttEl && caps.stt) {
      if (caps.stt.enabled && caps.stt.provider === 'gemini' && !caps.stt.ready) {
        sttEl.style.display = 'block';
        sttEl.textContent = '경고: STT가 Google AI Studio로 설정되어 있으나 API Key가 없어 음성 전사가 동작하지 않습니다.';
      } else {
        sttEl.style.display = 'none';
        sttEl.textContent = '';
      }
    }

    const sumEl = document.getElementById('prov-cap-summary');
    if (sumEl) {
      const locks = Array.isArray(caps.lock_reasons) ? caps.lock_reasons : [];
      const warns = Array.isArray(caps.warnings) ? caps.warnings : [];
      if (locks.length > 0) {
        sumEl.style.display = 'block';
        sumEl.style.color = 'var(--err,#cf222e)';
        sumEl.style.background = 'rgba(207,34,46,0.08)';
        sumEl.style.borderColor = 'rgba(207,34,46,0.35)';
        sumEl.innerHTML = `<strong>저장 잠금 (기능성 미충족):</strong><ul style="margin:4px 0 0;padding-left:18px;">${locks.map(r => `<li>${esc(r)}</li>`).join('')}</ul>`;
      } else if (warns.length > 0) {
        sumEl.style.display = 'block';
        sumEl.style.color = 'var(--warn,#9a6700)';
        sumEl.style.background = 'rgba(154,103,0,0.08)';
        sumEl.style.borderColor = 'rgba(154,103,0,0.35)';
        sumEl.innerHTML = `<strong>기능 안내:</strong><ul style="margin:4px 0 0;padding-left:18px;">${warns.map(w => `<li>${esc(w)}</li>`).join('')}</ul>`;
      } else {
        sumEl.style.display = 'block';
        sumEl.style.color = 'var(--accent2,#1a7f37)';
        sumEl.style.background = 'rgba(26,127,55,0.08)';
        sumEl.style.borderColor = 'rgba(26,127,55,0.35)';
        sumEl.textContent = '기능성 체크 통과: 선택한 프로바이더와 모델을 저장할 수 있습니다.';
      }
    }

    const saveBtn = document.getElementById('prov-save-btn');
    if (saveBtn) {
      const canSave = Boolean(caps.can_save);
      saveBtn.disabled = !canSave;
      saveBtn.style.opacity = canSave ? '1' : '0.45';
      saveBtn.style.cursor = canSave ? 'pointer' : 'not-allowed';
      saveBtn.title = canSave ? '' : (caps.lock_reasons || []).join(' / ');
    }
  }

  async function runProviderCapabilityCheck(options) {
    if (!_provState.config) return;
    const opts = options || {};
    if (_capAbortCtrl) _capAbortCtrl.abort();
    _capAbortCtrl = new AbortController();
    const saveBtn = document.getElementById('prov-save-btn');
    const sumEl = document.getElementById('prov-cap-summary');
    if (saveBtn) saveBtn.disabled = true;
    if (sumEl) {
      sumEl.style.display = 'block';
      sumEl.style.color = 'var(--muted,#6e7781)';
      sumEl.style.background = 'rgba(136,136,136,0.08)';
      sumEl.style.borderColor = 'var(--border)';
      sumEl.textContent = '기능성 체크 진행 중…';
    }
    const payload = collectProviderFormPayload();
    if (opts.live) payload.live = true;
    if (opts.refreshAgy) payload.refresh_agy = true;
    try {
      const url = resolveRootUrl(_provState.opts, 'providers/check');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: _capAbortCtrl.signal });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const caps = await r.json();
      applyProviderCapabilities(caps);
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      if (sumEl) {
        sumEl.style.display = 'block';
        sumEl.style.color = 'var(--err,#cf222e)';
        sumEl.style.background = 'rgba(207,34,46,0.08)';
        sumEl.style.borderColor = 'rgba(207,34,46,0.35)';
        sumEl.textContent = '기능성 체크 실패로 저장이 잠겼습니다: ' + (e.message || e);
      }
      if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.style.opacity = '0.45';
        saveBtn.style.cursor = 'not-allowed';
      }
    }
  }

  function bindProviderCapabilityEvents() {
    const standardIds = [
      'prov-active-select',
      'prov-gemini-key',
      'prov-gemini-model',
      'prov-gemini-embed',
      'prov-gemini-effort',
      'prov-jev-enabled',
      'prov-jev-mode',
      'prov-jev-filter-threshold',
      'prov-jev-auto-merge-threshold',
      'prov-stt-enabled',
      'prov-stt-prov',
      'prov-stt-lang'
    ];
    for (const id of standardIds) {
      const el = document.getElementById(id);
      if (!el) continue;
      el.addEventListener('change', () => {
        if (el.type === 'password' && el.value) delete el.dataset.cleared;
        refreshLineageVisualizerPreview();
        runProviderCapabilityCheck();
      });
    }
    const tierProvEl = document.getElementById('prov-tier-provider');
    const tierModelEl = document.getElementById('prov-tier-model');
    const tierEffEl = document.getElementById('prov-tier-max-effort');
    if (tierProvEl && tierModelEl && tierEffEl) {
      const syncTierState = () => {
        const isNone = tierProvEl.value === 'none';
        tierModelEl.disabled = isNone;
        tierEffEl.disabled = isNone;
      };
      tierProvEl.addEventListener('change', syncTierState);
      syncTierState();
    }
    const slotNames = ['extract_graph', 'summarize', 'entity_judge', 'relation_judge', 'classify', 'research', 'embed', 'stt', 'vlm'];
    for (const slot of slotNames) {
      const pEl = document.getElementById(`prov-route-${slot}-provider`);
      const mEl = document.getElementById(`prov-route-${slot}-model`);
      const eEl = document.getElementById(`prov-route-${slot}-effort`);
      if (pEl) {
        pEl.addEventListener('change', () => {
          updateRouteSlotModelOptions(slot);
          refreshLineageVisualizerPreview();
          runProviderCapabilityCheck();
        });
      }
      if (mEl) {
        mEl.addEventListener('change', () => {
          refreshLineageVisualizerPreview();
          runProviderCapabilityCheck();
        });
      }
      if (eEl) {
        eEl.addEventListener('change', () => {
          refreshLineageVisualizerPreview();
          runProviderCapabilityCheck();
        });
      }
    }
  }

  async function testProviderConnection(provName) {
    const resEl = document.getElementById('prov-test-res-' + provName);
    if (resEl) {
      resEl.style.display = 'block';
      resEl.style.color = 'var(--muted,#888)';
      resEl.textContent = '테스트 실행 중…';
    }
    const payload = { provider: provName, config: {} };
    if (provName === 'gemini') {
      const kEl = document.getElementById('prov-gemini-key');
      if (kEl && kEl.value) payload.config.api_key = kEl.value;
      const mEl = document.getElementById('prov-gemini-model');
      if (mEl && mEl.value) payload.config.model = mEl.value;
    } else if (provName === 'antigravity') {
      const bEl = document.getElementById('prov-agy-bin');
      if (bEl && bEl.value) payload.config.bin = bEl.value;
    } else if (provName === 'codex') {
      const bEl = document.getElementById('prov-cdx-bin');
      if (bEl && bEl.value) payload.config.bin = bEl.value;
    } else if (provName === 'openai') {
      const kEl = document.getElementById('prov-oai-key');
      if (kEl && kEl.value) payload.config.api_key = kEl.value;
    }
    try {
      const url = resolveRootUrl(_provState.opts, 'providers/test');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const d = await r.json();
      if (resEl) {
        if (d.ok) {
          resEl.style.color = 'var(--accent2,#1a7f37)';
          resEl.textContent = '' + (d.message || '연결 성공');
          if (provName === 'gemini' && d.details && Array.isArray(d.details.models) && d.details.models.length) {
            if (_provState.config && _provState.config.providers && _provState.config.providers.gemini) {
              _provState.config.providers.gemini.available_models = d.details.models;
              if (Array.isArray(d.details.embed_models)) _provState.config.providers.gemini.available_embed_models = d.details.embed_models;
            }
            const gSel = document.getElementById('prov-gemini-model');
            if (gSel) {
              const r1 = buildModelSelectResult(d.details.models, null, gSel.value, 'gemini-3.8-flash');
              gSel.innerHTML = r1.optionsHtml;
              gSel.value = r1.selected;
            }
            const eSel = document.getElementById('prov-gemini-embed');
            if (eSel && Array.isArray(d.details.embed_models) && d.details.embed_models.length) {
              const r2 = buildModelSelectResult(d.details.embed_models, null, eSel.value, 'gemini-embedding-001');
              eSel.innerHTML = r2.optionsHtml;
              eSel.value = r2.selected;
            }
            for (const sl of ['extract_graph', 'summarize', 'entity_judge', 'relation_judge', 'classify', 'research', 'embed', 'stt', 'vlm']) {
              updateRouteSlotModelOptions(sl);
            }
          } else if (provName === 'antigravity' && d.details && Array.isArray(d.details.models) && d.details.models.length) {
            const sel = document.getElementById('prov-agy-model');
            if (sel) {
              const res = buildModelSelectResult(d.details.models, null, sel.value, 'gemini-3.8-flash-medium');
              sel.innerHTML = res.optionsHtml;
              sel.value = res.selected;
              sel.disabled = Boolean(res.locked);
            }
          }
        } else {
          resEl.style.color = 'var(--err,#cf222e)';
          resEl.textContent = '' + (d.error || '연결 실패');
        }
      }
      await runProviderCapabilityCheck({ live: provName === 'gemini', refreshAgy: provName === 'antigravity' });
    } catch (e) {
      if (resEl) {
        resEl.style.color = 'var(--err,#cf222e)';
        resEl.textContent = '통신 오류: ' + (e.message || e);
      }
    }
  }

  async function saveProviderSettings() {
    if (!_provState.config) return;
    if (_provState.capabilities && _provState.capabilities.can_save === false) return;
    const updatePayload = collectProviderFormPayload();

    try {
      const url = resolveRootUrl(_provState.opts, 'providers');
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(updatePayload) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        renderProviderManagerDOM('저장 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      const resData = await r.json();
      _provState.config = resData.config;
      renderProviderManagerDOM('프로바이더 설정이 성공적으로 저장되었습니다 (data/providers.json 반영 완료).', true);
      if (_provState.opts && typeof _provState.opts.onSave === 'function') {
        _provState.opts.onSave(resData);
      }
    } catch (e) {
      renderProviderManagerDOM('오류 발생: ' + (e.message || e), false);
    }
  }

  const FEATURE_SLOT_DEFS = [
    { slot: 'extract_graph', label: '지식 추출 · 적재', note: '엔티티·관계 구조화 추출' },
    { slot: 'summarize', label: '요약 · 가독 상세', note: '적재와 분리해 별도 요약 모델 지정 가능' },
    { slot: 'entity_judge', label: '엔티티 병합 판정', note: 'System 2 LLM 병합 심사 (JEV 이후)' },
    { slot: 'relation_judge', label: '관계 연결 판정', note: 'Phase 2 교차 관계 방향·타입 판정' },
    { slot: 'classify', label: '문서 · 논문 분류', note: 'PDF 논문 및 감시 정책 판정' },
    { slot: 'research', label: '웹 리서치 · 종합', note: '검색 질의 종합 및 후속 링크 평가' },
    { slot: 'embed', label: '벡터 임베딩', note: '엔티티·문서 임베딩 (CLI는 gemini 키 사용)' },
    { slot: 'stt', label: '음성 전사 (STT)', note: '영상·오디오 자막 생성' },
    { slot: 'vlm', label: '시각 전사 (VLM)', note: '이미지 내용 텍스트 변환' }
  ];

  function resolveEffectiveRouteProvider(cfg, selectedRouteProv) {
    if (!selectedRouteProv || selectedRouteProv === 'inherit') {
      const actEl = document.getElementById('prov-active-select');
      return actEl ? actEl.value : (cfg.active_provider || 'mock');
    }
    return selectedRouteProv;
  }

  function modelsForRouteProvider(cfg, effProv, slot) {
    const provs = (cfg && cfg.providers) || {};
    if (slot === 'embed') {
      if (effProv === 'openai') {
        const list = normalizeModelOptions((provs.openai || {}).available_embed_models, ['text-embedding-3-small', 'text-embedding-3-large']);
        return [{ id: '', label: '기본 임베딩 모델 상속' }, ...list];
      }
      if (effProv === 'mock') return [{ id: '', label: 'mock-embed-64d (기본)' }];
      const gemList = normalizeModelOptions((provs.gemini || {}).available_embed_models, ['gemini-embedding-001', 'gemini-embedding-2', 'gemini-embedding-2-preview']);
      return [{ id: '', label: '기본 임베딩 모델 상속' }, ...gemList];
    }
    if (effProv === 'gemini') {
      const list = normalizeModelOptions((provs.gemini || {}).available_models, ['gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-4-hydrogen', 'gemini-4-argon', 'gemini-3.1-pro-preview', 'gemini-2.5-flash']);
      return [{ id: '', label: '기본 모델 상속' }, ...list];
    }
    if (effProv === 'antigravity') {
      const list = normalizeModelOptions((provs.antigravity || {}).available_models, []);
      return [{ id: '', label: '기본 모델 상속' }, ...list];
    }
    if (effProv === 'codex') {
      const list = normalizeModelOptions((provs.codex || {}).available_models, ['gpt-5.4', 'gpt-5.3-codex', 'o3', 'o4-mini']);
      return [{ id: '', label: '기본 모델 상속' }, ...list];
    }
    if (effProv === 'openai') {
      const list = normalizeModelOptions((provs.openai || {}).available_models, ['gpt-4o-mini', 'gpt-4o', 'gpt-5']);
      return [{ id: '', label: '기본 모델 상속' }, ...list];
    }
    return [{ id: '', label: 'mock (기본)' }];
  }

  function updateRouteSlotModelOptions(slot) {
    const cfg = _provState.config || {};
    const pEl = document.getElementById(`prov-route-${slot}-provider`);
    const mEl = document.getElementById(`prov-route-${slot}-model`);
    const eEl = document.getElementById(`prov-route-${slot}-effort`);
    if (!pEl || !mEl) return;
    const effProv = resolveEffectiveRouteProvider(cfg, pEl.value);
    const items = modelsForRouteProvider(cfg, effProv, slot);
    const prev = mEl.value;
    let html = '';
    let hasPrev = false;
    for (const item of items) {
      if (item.id === prev) hasPrev = true;
      const text = item.id === '' ? (item.label || '기본 모델 상속') : item.id;
      html += `<option value="${esc(item.id)}" ${item.id === prev ? 'selected' : ''}>${esc(text)}</option>`;
    }
    if (prev && !hasPrev) {
      html += `<option value="${esc(prev)}" selected>${esc(prev)} (현재 값)</option>`;
    }
    mEl.innerHTML = html;
    if (eEl) eEl.disabled = (slot === 'embed' || effProv === 'mock');
  }

  function computeEffectiveSlotsFromDom(cfg) {
    const payload = document.getElementById('prov-active-select') ? collectProviderFormPayload() : null;
    const summarySlots = (cfg.lineage_summary && cfg.lineage_summary.effective_slots) || {};
    if (!payload) return summarySlots;
    const active = payload.active_provider || cfg.active_provider || 'mock';
    const provs = payload.providers || {};
    const out = {};
    for (const def of FEATURE_SLOT_DEFS) {
      const slot = def.slot;
      const r = (payload.feature_routing && payload.feature_routing[slot]) || { provider: 'inherit', model: '', effort: '' };
      let effProv = (!r.provider || r.provider === 'inherit') ? active : r.provider;
      let baseModel = '';
      let baseEffort = '';
      if (effProv === 'gemini') {
        baseModel = slot === 'embed' ? (provs.gemini.embed_model || 'gemini-embedding-001') : (provs.gemini.model || '');
        baseEffort = slot === 'embed' ? '' : (provs.gemini.effort || '');
      } else if (effProv === 'antigravity') {
        baseModel = slot === 'embed' ? (provs.gemini.embed_model || 'gemini-embedding-001') : (provs.antigravity.model || '');
        baseEffort = slot === 'embed' ? '' : (provs.antigravity.effort || '');
      } else if (effProv === 'codex') {
        baseModel = slot === 'embed' ? (provs.gemini.embed_model || 'gemini-embedding-001') : (provs.codex.model || 'codex-default');
        baseEffort = slot === 'embed' ? '' : (provs.codex.effort || '');
      } else if (effProv === 'openai') {
        baseModel = slot === 'embed' ? (provs.openai.embed_model || '') : (provs.openai.model || '');
      } else if (effProv === 'mock') {
        baseModel = slot === 'embed' ? 'mock-embed-64d' : 'mock';
      }
      out[slot] = { provider: effProv, model: r.model || baseModel, effort: r.effort || baseEffort };
    }
    return out;
  }

  function renderLineageSvg(effectiveSlots, jevCfg) {
    const slotText = (slot) => {
      const s = effectiveSlots[slot] || {};
      const p = s.provider || '-';
      const m = (s.model || '-').slice(0, 22);
      const e = s.effort ? ` (${s.effort})` : '';
      return { line1: `${p}`, line2: `${m}${e}` };
    };
    const eg = slotText('extract_graph');
    const sm = slotText('summarize');
    const em = slotText('embed');
    const ej = slotText('entity_judge');
    const rj = slotText('relation_judge');
    const jevLabel = jevCfg && jevCfg.enabled ? `jev:${jevCfg.mode || 'filter'} (Gate2·3 후)` : 'jev:off (LLM 직행)';
    const jevSub = jevCfg && jevCfg.enabled ? `<${jevCfg.filter_threshold ?? 0.2} 제외 / 단독>=${jevCfg.auto_merge_threshold ?? 0.9}` : 'LLM 판정 직접 실행';
    const box = (x, y, w, h, title, sub1, sub2, stroke) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="var(--sec-bg,#f6f8fa)" stroke="${stroke || '#6e7781'}" stroke-width="1.4"/><text x="${x + w / 2}" y="${y + 18}" text-anchor="middle" font-size="11" font-weight="700" fill="currentColor">${esc(title)}</text><text x="${x + w / 2}" y="${y + 34}" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.85">${esc(sub1)}</text><text x="${x + w / 2}" y="${y + 49}" text-anchor="middle" font-size="9.5" fill="currentColor" opacity="0.72">${esc(sub2)}</text>`;
    const arrow = (x1, y1, x2, y2, label) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#6e7781" stroke-width="1.4" marker-end="url(#prov-lineage-arrow)"/>${label ? `<text x="${(x1 + x2) / 2}" y="${Math.min(y1, y2) - 6}" text-anchor="middle" font-size="8.5" fill="currentColor" opacity="0.78">${esc(label)}</text>` : ''}`;
    let svg = '<svg id="prov-lineage-svg" viewBox="0 0 920 196" width="100%" style="max-width:920px;height:auto;display:block;border:1px solid var(--border);border-radius:6px;background:var(--card-bg,#fff);color:var(--fg,#1f2328);">';
    svg += '<defs><marker id="prov-lineage-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#6e7781"/></marker></defs>';
    svg += box(10, 62, 118, 64, '1. 입력·파싱', 'F10 비-LLM', 'PDF/Web/STT/VLM', '#6e7781');
    svg += arrow(128, 82, 152, 46);
    svg += arrow(128, 104, 152, 138);
    svg += box(154, 14, 156, 64, '2A. 지식 추출', eg.line1, eg.line2, '#0969da');
    svg += box(154, 108, 156, 64, '2B. 요약·상세', sm.line1, sm.line2, '#8250df');
    svg += arrow(310, 46, 338, 72);
    svg += arrow(310, 140, 338, 106);
    svg += box(340, 54, 166, 68, '3. 벡터 임베딩 (1차)', `${em.line1}:${em.line2}`, 'DOC색인/QUERY질의 >=0.72', '#1a7f37');
    svg += arrow(506, 88, 558, 88, '자연어JSON');
    svg += box(560, 54, 168, 68, '4. JEV System 1', jevLabel, jevSub, '#9a6700');
    svg += arrow(728, 76, 752, 46);
    svg += arrow(728, 98, 752, 136);
    svg += box(754, 14, 156, 64, '5A. 병합·관계 판정', `${ej.line1} / ${rj.line1}`, `${ej.line2}`, '#bf3989');
    svg += box(754, 108, 156, 64, '5B. 최종 지식 그래프', 'doc.meta.model_lineage', 'LLM 폴백 0건 (엄격 실패)', '#1f883d');
    svg += '</svg>';
    return svg;
  }

  function refreshLineageVisualizerPreview() {
    const cfg = _provState.config;
    const host = document.getElementById('prov-lineage-svg-host');
    if (!cfg || !host) return;
    const jevEnabledEl = document.getElementById('prov-jev-enabled');
    const jevModeEl = document.getElementById('prov-jev-mode');
    const jevFilterEl = document.getElementById('prov-jev-filter-threshold');
    const jevAutoEl = document.getElementById('prov-jev-auto-merge-threshold');
    const jevSnap = {
      enabled: jevEnabledEl ? jevEnabledEl.checked : Boolean(((cfg.providers || {}).jev || {}).enabled),
      mode: jevModeEl ? jevModeEl.value : (((cfg.providers || {}).jev || {}).mode || 'filter'),
      filter_threshold: jevFilterEl ? jevFilterEl.value : (((cfg.providers || {}).jev || {}).filter_threshold ?? 0.20),
      auto_merge_threshold: jevAutoEl ? jevAutoEl.value : (((cfg.providers || {}).jev || {}).auto_merge_threshold ?? 0.90)
    };
    host.innerHTML = renderLineageSvg(computeEffectiveSlotsFromDom(cfg), jevSnap);
  }

  function renderPipelineLineageHtml(cfg) {
    const provs = cfg.providers || {};
    const jev = provs.jev || {};
    const lineage = cfg.lineage_summary || {};
    let h = '<div id="prov-lineage-visualizer-card" style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:4px;"><span data-icon="git-branch"></span> 파이프라인 모델 계보 및 아키텍처 명세 (Pipeline Lineage)</strong>';
    h += '<p style="margin:0 0 8px;font-size:11px;opacity:0.8;">문서 적재·요약·임베딩(1차 후보 검색)·JEV System 1(자연어 동일성 판정)에서 최종 산출까지 실제로 어떤 모델과 고정 파라미터가 관여하는지 시각화합니다.</p>';
    h += `<div id="prov-lineage-svg-host" style="margin-bottom:10px;">${renderLineageSvg(computeEffectiveSlotsFromDom(cfg), jev)}</div>`;

    const jevStats = lineage.jev || {};
    h += `<div style="font-size:11px;margin-bottom:8px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--card-bg,#fff);">JEV 텔레메트리: 누적 쌍 <b>${Number(jevStats.shadow_pairs || 0)}</b> · 정상 응답 <b>${Number(jevStats.ok_calls || 0)}</b> · 평균 지연 <b>${jevStats.avg_latency_ms != null ? jevStats.avg_latency_ms + 'ms' : '-'}</b> · 필터 임계값 미만 <b>${Number(jevStats.below_filter_threshold || 0)}</b></div>`;

    const specs = Array.isArray(lineage.architecture_specs) ? lineage.architecture_specs : [];
    if (specs.length > 0) {
      h += '<details open style="margin-bottom:8px;font-size:11px;"><summary style="cursor:pointer;font-weight:600;">아키텍처 고정 정책 · 파라미터 명세 (코드 기여·이슈 리포트 기준)</summary>';
      h += '<table id="prov-architecture-spec-table" style="width:100%;border-collapse:collapse;margin-top:6px;font-size:11px;">';
      h += '<thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:3px 5px;">단계</th><th style="padding:3px 5px;">고정 값 / 정책</th><th style="padding:3px 5px;">코드 위치</th><th style="padding:3px 5px;">설계 근거</th></tr></thead><tbody>';
      for (const sp of specs) {
        h += `<tr style="border-bottom:1px solid var(--border);"><td style="padding:3px 5px;font-weight:600;">${esc(sp.stage)}</td><td style="padding:3px 5px;"><code>${esc(sp.fixed_value)}</code></td><td style="padding:3px 5px;"><code>${esc(sp.scope)}</code></td><td style="padding:3px 5px;">${esc(sp.rationale)}</td></tr>`;
      }
      h += '</tbody></table></details>';
    }

    const fallbacks = Array.isArray(lineage.fallbacks) ? lineage.fallbacks : [];
    if (fallbacks.length > 0) {
      h += '<details style="margin-bottom:8px;font-size:11px;"><summary style="cursor:pointer;font-weight:600;">폴백 감사 카탈로그 (F1–F10 현황)</summary>';
      h += '<table id="prov-fallback-catalog-table" style="width:100%;border-collapse:collapse;margin-top:6px;font-size:11px;">';
      h += '<thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:3px 5px;">ID</th><th style="padding:3px 5px;">위치</th><th style="padding:3px 5px;">상태</th><th style="padding:3px 5px;">설명</th></tr></thead><tbody>';
      for (const fb of fallbacks) {
        const badge = fb.status === 'REMOVED' ? '제거됨 (엄격 실패)' : '대상 제외 (비-LLM)';
        h += `<tr style="border-bottom:1px solid var(--border);"><td style="padding:3px 5px;"><code>${esc(fb.id)}</code></td><td style="padding:3px 5px;"><code>${esc(fb.location)}</code></td><td style="padding:3px 5px;">${esc(badge)}</td><td style="padding:3px 5px;">${esc(fb.description)}</td></tr>`;
      }
      h += '</tbody></table></details>';
    }

    const recDocs = Array.isArray(lineage.recent_documents) ? lineage.recent_documents : [];
    h += '<details style="font-size:11px;"' + (recDocs.length > 0 ? ' open' : '') + '><summary style="cursor:pointer;font-weight:600;">최근 적재 문서 모델 계보 (' + recDocs.length + '건)</summary>';
    if (recDocs.length === 0) {
      h += '<p style="margin:6px 0 0;opacity:0.75;">아직 <code>model_lineage</code>가 기록된 최근 문서가 없습니다. 문서를 적재하면 단계별 실제 실행 모델이 이곳에 표시됩니다.</p>';
    } else {
      h += '<table id="prov-recent-lineage-table" style="width:100%;border-collapse:collapse;margin-top:6px;font-size:11px;">';
      h += '<thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:3px 5px;">문서</th><th style="padding:3px 5px;">최종 단계별 모델</th><th style="padding:3px 5px;">JEV</th><th style="padding:3px 5px;">폴백</th></tr></thead><tbody>';
      for (const d of recDocs.slice(0, 8)) {
        const lin = d.lineage || {};
        const finals = lin.final_output_models || {};
        const modelTags = Object.entries(finals).map(([st, info]) => `${st}=${(info && info.provider) || '?'}:${(info && info.model) || '?'}`).join(', ');
        const jevD = lin.jev || {};
        const jevSummary = jevD.enabled ? `${jevD.mode || 'filter'} (filtered:${Number(jevD.filtered_count || 0)}, auto:${Number(jevD.hybrid_auto_merged_count || 0)})` : 'off';
        const fbCount = Array.isArray(lin.fallbacks_triggered) ? lin.fallbacks_triggered.length : 0;
        h += `<tr style="border-bottom:1px solid var(--border);"><td style="padding:3px 5px;">${esc(d.title || d.id)}</td><td style="padding:3px 5px;"><code>${esc(modelTags || '-')}</code></td><td style="padding:3px 5px;">${esc(jevSummary)}</td><td style="padding:3px 5px;">${fbCount}</td></tr>`;
      }
      h += '</tbody></table>';
    }
    h += '</details></div>';
    return h;
  }

  function renderFeatureRoutingHtml(cfg) {
    const routing = cfg.feature_routing || {};
    const routeProvChoices = [
      { id: 'inherit', label: '메인 프로바이더 상속' },
      { id: 'gemini', label: 'Google AI Studio (gemini)' },
      { id: 'mock', label: 'Mock' }
    ];

    let h = '<div id="prov-feature-routing-card" style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:4px;"><span data-icon="layers"></span> 기능별 모델 라우팅 (Feature Routing)</strong>';
    h += '<p style="margin:0 0 8px;font-size:11px;opacity:0.8;">적재(extract_graph)와 요약(summarize), 판정(judge), 임베딩을 기능별로 다른 모델에 배정합니다. 멤버 BYOK 사용자는 여기서 관리자가 지정한 기준선 동등 이상 모델만 선택할 수 있습니다.</p>';
    h += '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:11.5px;">';
    h += '<thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:4px 6px;">기능 슬롯</th><th style="padding:4px 6px;">프로바이더</th><th style="padding:4px 6px;">모델</th><th style="padding:4px 6px;">추론 수준</th></tr></thead><tbody>';
    for (const def of FEATURE_SLOT_DEFS) {
      const r = routing[def.slot] || { provider: 'inherit', model: '', effort: '' };
      const rProv = r.provider || 'inherit';
      const effProv = resolveEffectiveRouteProvider(cfg, rProv);
      const modelOpts = modelsForRouteProvider(cfg, effProv, def.slot);
      h += `<tr style="border-bottom:1px solid var(--border);" data-route-slot="${esc(def.slot)}">`;
      h += `<td style="padding:5px 6px;"><div style="font-weight:600;">${esc(def.label)}</div><div style="font-size:10px;opacity:0.7;"><code>${esc(def.slot)}</code> · ${esc(def.note)}</div></td>`;
      h += `<td style="padding:5px 6px;"><select id="prov-route-${esc(def.slot)}-provider" style="width:100%;box-sizing:border-box;">`;
      for (const opt of routeProvChoices) {
        h += `<option value="${esc(opt.id)}" ${rProv === opt.id ? 'selected' : ''}>${esc(opt.label)}</option>`;
      }
      h += '</select></td>';
      h += `<td style="padding:5px 6px;"><select id="prov-route-${esc(def.slot)}-model" style="width:100%;box-sizing:border-box;">`;
      let matchedModel = false;
      for (const m of modelOpts) {
        if (m.id === (r.model || '')) matchedModel = true;
        const mLabel = m.id === '' ? (m.label || '기본 모델 상속') : m.id;
        h += `<option value="${esc(m.id)}" ${(r.model || '') === m.id ? 'selected' : ''}>${esc(mLabel)}</option>`;
      }
      if (r.model && !matchedModel) {
        h += `<option value="${esc(r.model)}" selected>${esc(r.model)} (현재 값)</option>`;
      }
      h += '</select></td>';
      const effortDis = (def.slot === 'embed' || effProv === 'mock') ? ' disabled' : '';
      h += `<td style="padding:5px 6px;"><select id="prov-route-${esc(def.slot)}-effort"${effortDis} style="width:100%;box-sizing:border-box;">`;
      for (const eOpt of [['', '기본 상속'], ['low', 'low'], ['medium', 'medium'], ['high', 'high']]) {
        h += `<option value="${esc(eOpt[0])}" ${(r.effort || '') === eOpt[0] ? 'selected' : ''}>${esc(eOpt[1])}</option>`;
      }
      h += '</select></td>';
      h += '</tr>';
    }
    h += '</tbody></table></div></div>';
    return h;
  }

  function renderMemberTierCardHtml(cfg) {
    const tier = cfg.server_tier || { provider: 'none', model: '', max_effort: 'medium', features: [] };
    const gemini = (cfg.providers || {}).gemini || {};
    const geminiModels = normalizeModelOptions(gemini.available_models, [
      'gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-4-argon', 'gemini-3.6-flash',
      'gemini-3.1-flash-lite', 'gemini-3.1-pro-preview', 'gemini-3-flash-preview',
      'gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.5-pro'
    ]);
    const tierFeats = Array.isArray(tier.features) ? tier.features : [];
    const featLabels = [
      ['expand', '확장 (expand)'],
      ['stt', '음성 전사 (stt)'],
      ['vlm', '이미지 해석 (vlm)'],
      ['presentation', '프레젠테이션'],
      ['synthesize', '종합 (synthesize)']
    ];
    let h = '<div id="prov-member-tier-card" style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:4px;"><span data-icon="shield"></span> 멤버 서버 등급 제공 정책 (ServerTier)</strong>';
    h += '<p style="margin:0 0 8px;font-size:11px;opacity:0.8;">자체 BYOK 키가 없는 멤버에게 제공할 서버 등급 프로바이더, 기준 모델, 최대 추론 수준과 허용 기능을 이곳에서 통합 관리합니다. (토큰·저장량 수치 한도는 <a href="/admin/instance">인스턴스 관리</a>에서 설정)</p>';
    h += '<div style="display:flex;gap:8px;font-size:12px;margin-bottom:8px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">제공 프로바이더</label>';
    h += '<select id="prov-tier-provider" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="none" ${(tier.provider || 'none') === 'none' ? 'selected' : ''}>제공 안 함 (none - BYOK 필수)</option>`;
    h += `<option value="gemini" ${tier.provider === 'gemini' ? 'selected' : ''}>Google AI Studio (gemini)</option>`;
    h += '</select></div>';
    h += '<div style="flex:1.2;"><label style="font-size:11px;opacity:0.8;">서버 등급 모델</label>';
    h += '<select id="prov-tier-model" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="" ${!tier.model ? 'selected' : ''}>선택 안 함</option>`;
    let matched = false;
    for (const m of geminiModels) {
      if (m.id === tier.model) matched = true;
      h += `<option value="${esc(m.id)}" ${tier.model === m.id ? 'selected' : ''}>${esc(m.id)}</option>`;
    }
    if (tier.model && !matched) {
      h += `<option value="${esc(tier.model)}" selected>${esc(tier.model)}</option>`;
    }
    h += '</select></div>';
    h += '<div style="flex:0.8;"><label style="font-size:11px;opacity:0.8;">최대 추론 수준 (Effort)</label>';
    h += '<select id="prov-tier-max-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    for (const eff of ['none', 'minimal', 'low', 'medium', 'high']) {
      h += `<option value="${eff}" ${(tier.max_effort || 'medium') === eff ? 'selected' : ''}>${eff}</option>`;
    }
    h += '</select></div>';
    h += '</div>';
    h += '<div style="font-size:11px;"><span style="opacity:0.8;display:block;margin-bottom:4px;">멤버 허용 기능</span><div style="display:flex;flex-wrap:wrap;gap:10px;">';
    for (const [fKey, fLabel] of featLabels) {
      const checked = tierFeats.includes(fKey) ? ' checked' : '';
      h += `<label style="cursor:pointer;"><input type="checkbox" name="prov-tier-feature" value="${esc(fKey)}"${checked} style="width:auto;margin-right:4px;vertical-align:middle;"/>${esc(fLabel)}</label>`;
    }
    h += '</div></div></div>';
    return h;
  }

  function renderProviderManagerDOM(msg, isSuccess) {
    const target = _provState.container;
    if (!target || !_provState.config) return;
    const cfg = _provState.config;
    const provs = cfg.providers || {};
    const gemini = provs.gemini || {};
    const jev = provs.jev || {};
    const stt = provs.stt || {};
    const active = cfg.active_provider || 'mock';

    let alertHtml = '';
    if (msg) {
      const color = isSuccess ? 'var(--accent2,#1a7f37)' : 'var(--err,#cf222e)';
      const bg = isSuccess ? 'rgba(26,127,55,0.1)' : 'rgba(207,34,46,0.1)';
      const icon = isSuccess ? '<span data-icon="check-circle"></span>' : '<span data-icon="warning"></span>';
      alertHtml = `<div style="padding:8px 10px;border-radius:6px;background:${bg};color:${color};font-size:12px;margin-bottom:12px;border:1px solid ${color};">${icon} ${esc(msg)}</div>`;
    }

    let h = '<h2><span data-icon="bolt"></span> LLM &amp; 프로바이더 관리</h2>';
    h += '<p class="al">서버 공용 하이퍼스케일러(Google AI Studio), 기능별 모델 라우팅, 멤버 ServerTier 제공 정책을 설정합니다. 변경 사항은 <code>data/providers.json</code>에 영구 저장됩니다.</p>';
    h += alertHtml;

    h += renderPipelineLineageHtml(cfg);

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<label style="font-weight:600;font-size:13px;display:block;margin-bottom:6px;">서버 공용 메인 LLM 프로바이더</label>';
    h += '<select id="prov-active-select" style="width:100%;padding:6px 8px;font-size:13px;border-radius:6px;border:1px solid var(--border);background:var(--card-bg);color:var(--fg);box-sizing:border-box;">';
    const opts = [
      {val: 'gemini', label: 'Google AI Studio (권장 - 고품질 LLM 및 임베딩)'},
      {val: 'mock', label: 'Mock (가상 테스트 프로바이더 - 외부 호출 없음)'}
    ];
    for (const opt of opts) {
      h += `<option value="${opt.val}" ${active === opt.val ? 'selected' : ''}>${opt.label}</option>`;
    }
    h += '</select>';
    h += `<div style="margin-top:6px;font-size:11px;opacity:0.8;">현재 동작 중: <b>${esc(cfg.effective_provider || active)}</b> · STT 동작: <b>${esc(cfg.effective_stt_provider || 'mock')}</b> · 개인 CLI(Antigravity · Codex)는 약관 정책상 <a href="/settings/keys">개인 계정 설정(/settings/keys)</a>에서 별도 관리합니다.</div>`;
    h += '<div id="prov-feat-mock" style="margin-top:6px;"></div>';
    h += '<div id="prov-cap-mock" style="display:none;margin-top:4px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;font-size:11px;"></div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">';
    h += '<strong style="font-size:13px;"><span data-icon="globe"></span> Google AI Studio</strong>';
    h += '<button type="button" class="sec" style="font-size:11px;padding:2px 8px;" onclick="testProviderConnection(\'gemini\')">API 확인 · 모델 조회</button>';
    h += '</div>';
    h += '<div id="prov-feat-gemini"></div>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div><label style="font-size:11px;opacity:0.8;">Google AI Studio API Key</label>';
    h += '<div style="display:flex;gap:4px;margin-top:2px;">';
    h += `<input id="prov-gemini-key" type="password" style="flex:1;box-sizing:border-box;" placeholder="${gemini.has_api_key ? '설정됨 (••••••••)' : 'Google AI Studio API Key 입력'}"/>`;
    h += '<button type="button" class="sec" style="font-size:11px;padding:2px 6px;" onclick="togglePassVisibility(\'prov-gemini-key\')" title="'+esc(trKey('console.providers.toggle_key'))+'" aria-label="'+esc(trKey('console.providers.toggle_key'))+'"><span data-icon="eye"></span></button>';
    if (gemini.has_api_key) {
      h += '<button type="button" class="sec danger-btn" style="font-size:11px;padding:2px 6px;color:var(--err,#cf222e);" onclick="clearApiKeyInput(\'prov-gemini-key\', \'gemini\')">삭제</button>';
    }
    h += '</div></div>';
    const geminiModelFallback = [
      'gemini-3.5-flash-lite',
      'gemini-3.8-flash',
      'gemini-4-argon',
      'gemini-3.6-flash',
      'gemini-3.1-flash-lite',
      'gemini-3.1-pro-preview',
      'gemini-3-flash-preview',
      'gemini-2.5-flash',
      'gemini-2.5-flash-lite',
      'gemini-2.5-pro'
    ];
    const geminiEmbedFallback = ['gemini-embedding-001', 'gemini-embedding-2', 'gemini-embedding-2-preview'];
    h += '<div><label style="font-size:11px;opacity:0.8;">모델 (Model · API 확인 시 지원 목록 자동 갱신)</label>';
    h += renderModelSelectField('prov-gemini-model', gemini.available_models, geminiModelFallback, gemini.model || 'gemini-3.8-flash', 'gemini-3.8-flash');
    h += '</div>';
    h += '<div style="display:flex;gap:8px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">추론 수준 (Effort)</label>';
    h += '<select id="prov-gemini-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="low" ${gemini.effort==='low'?'selected':''}>low</option>`;
    h += `<option value="medium" ${gemini.effort==='medium'||!gemini.effort?'selected':''}>medium</option>`;
    h += `<option value="high" ${gemini.effort==='high'?'selected':''}>high</option>`;
    h += '</select></div>';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">임베딩 모델 (문서 색인: RETRIEVAL_DOCUMENT / 검색: RETRIEVAL_QUERY 고정)</label>';
    h += renderModelSelectField('prov-gemini-embed', gemini.available_embed_models, geminiEmbedFallback, gemini.embed_model || 'gemini-embedding-001', 'gemini-embedding-001');
    h += '</div>';
    h += '</div>';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">최소 간격 (초)</label><input id="prov-gemini-interval" type="number" step="0.5" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${gemini.min_interval||4.0}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">최대 재시도 (회)</label><input id="prov-gemini-retries" type="number" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${gemini.max_retries||5}"/></div>`;
    h += '</div>';
    h += '</div>';
    h += '<div id="prov-cap-gemini" style="display:none;margin-top:6px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;font-size:11px;"></div>';
    h += '<div id="prov-test-res-gemini" style="display:none;margin-top:8px;font-size:11px;"></div>';
    h += '</div>';

    h += renderFeatureRoutingHtml(cfg);
    h += renderMemberTierCardHtml(cfg);

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="puzzle"></span> 보조 엔진 (Jev &amp; 음성 전사)</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:10px;font-size:12px;">';
    h += '<div>';
    h += `<label style="cursor:pointer;font-weight:600;"><input id="prov-jev-enabled" type="checkbox" ${jev.enabled?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>TypeSafe AI Jev (System 1 엔티티 판정 가속 · Filter / Hybrid / Shadow)</label>`;
    h += '<div style="margin-top:6px;display:flex;gap:4px;">';
    h += `<input id="prov-jev-key" type="password" style="flex:1;box-sizing:border-box;" placeholder="${jev.has_api_key ? 'Jev 키 설정됨 (••••••••)' : 'Jev API Key'}"/>`;
    h += '<button type="button" class="sec" style="font-size:11px;padding:2px 6px;" onclick="togglePassVisibility(\'prov-jev-key\')" title="'+esc(trKey('console.providers.toggle_key'))+'" aria-label="'+esc(trKey('console.providers.toggle_key'))+'"><span data-icon="eye"></span></button>';
    h += '</div>';
    h += `<input id="prov-jev-url" style="width:100%;box-sizing:border-box;margin-top:4px;" placeholder="Base URL" value="${esc(jev.base_url||'https://api.typesafe.ai/v1')}"/>`;
    const curJevMode = jev.mode || 'filter';
    const curFilterThr = jev.filter_threshold != null ? jev.filter_threshold : 0.20;
    const curAutoThr = jev.auto_merge_threshold != null ? jev.auto_merge_threshold : 0.90;
    h += '<div style="display:flex;gap:8px;margin-top:6px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">JEV 판정 모드</label><select id="prov-jev-mode" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="filter" ${curJevMode==='filter'?'selected':''}>filter (임계값 미만 LLM 생략 · 권장)</option>`;
    h += `<option value="hybrid" ${curJevMode==='hybrid'?'selected':''}>hybrid (필터 + 단독 고신뢰 자동 병합)</option>`;
    h += `<option value="shadow" ${curJevMode==='shadow'?'selected':''}>shadow (평가 기록만 · 비관여)</option>`;
    h += '</select></div>';
    h += `<div style="flex:0.7;"><label style="font-size:11px;opacity:0.8;">필터 임계값 (&lt; 생략)</label><input id="prov-jev-filter-threshold" type="number" step="0.05" min="0" max="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(String(curFilterThr))}"/></div>`;
    h += `<div style="flex:0.7;"><label style="font-size:11px;opacity:0.8;">자동 병합 임계값 (&gt;=)</label><input id="prov-jev-auto-merge-threshold" type="number" step="0.05" min="0" max="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(String(curAutoThr))}"/></div>`;
    h += '</div>';
    h += '</div>';
    h += '<div style="border-top:1px dashed var(--border);padding-top:8px;">';
    h += `<label style="cursor:pointer;font-weight:600;"><input id="prov-stt-enabled" type="checkbox" ${stt.enabled!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>비디오 &amp; 오디오 음성 전사 (STT)</label>`;
    h += '<div style="display:flex;gap:8px;margin-top:6px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">STT 프로바이더</label>';
    h += '<select id="prov-stt-prov" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="gemini" ${stt.provider==='gemini'?'selected':''}>Google AI Studio (API)</option>`;
    h += `<option value="mock" ${stt.provider==='mock'?'selected':''}>Mock</option>`;
    h += '</select></div>';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">언어 코드</label><input id="prov-stt-lang" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(stt.language||'ko')}"/></div>`;
    h += '</div>';
    h += '<div id="prov-cap-stt" style="display:none;margin-top:6px;padding:6px 8px;border:1px solid rgba(154,103,0,0.35);background:rgba(154,103,0,0.08);color:var(--warn,#9a6700);border-radius:4px;font-size:11px;"></div>';
    h += '</div>';
    h += '</div>';
    h += '</div>';

    const optsState = _provState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    h += '<div id="prov-cap-summary" style="display:none;margin-top:12px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font-size:12px;"></div>';
    h += '<div style="display:flex;gap:8px;margin-top:12px;">';
    h += '<button id="prov-save-btn" type="button" class="btn" style="flex:1;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="saveProviderSettings()"><span data-icon="save"></span> 설정 저장</button>';
    if (canClose) {
      h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="closeProviderManager()">닫기</button>';
    }
    h += '</div>';

    target.innerHTML = h;
    if (cfg.capabilities) applyProviderCapabilities(cfg.capabilities);
    bindProviderCapabilityEvents();
  }

  async function renderProviderManager(container, opts) {
    if (container instanceof HTMLElement || (container && container.nodeType === 1) || (typeof container === 'string' && (container.startsWith('#') || container.startsWith('.')))) {
      _provState.container = resolveContainer(container);
      _provState.opts = opts || {};
    } else if (typeof container === 'string') {
      const msg = container;
      const isSuccess = Boolean(opts);
      renderProviderManagerDOM(msg, isSuccess);
      return;
    }
    const target = _provState.container;
    if (!target) return;
    target.innerHTML = '<h2><span data-icon="bolt"></span> 프로바이더 관리</h2><p class="hint">설정 로딩 중…</p>';
    try {
      const url = resolveRootUrl(_provState.opts, 'providers');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _provState.config = await r.json();
      renderProviderManagerDOM();
    } catch (e) {
      target.innerHTML = `<h2><span data-icon="bolt"></span> 프로바이더 관리</h2><p class="hint" style="color:var(--err,#cf222e)">설정을 불러올 수 없습니다: ${esc(e.message || e)}</p>`;
    }
  }

  // ==========================================
  // 2. LIMIT MANAGER
  // ==========================================

  function closeLimitManager() {
    if (_limitState.opts && typeof _limitState.opts.onClose === 'function') {
      _limitState.opts.onClose();
    } else if (typeof window.closeDrawer === 'function') {
      window.closeDrawer();
    }
  }

  async function saveLimitSettings() {
    if (!_limitState.config) return;
    const rawBudget = parseInt(document.getElementById('limit-raw-char-budget').value, 10);
    const pdfMax = parseInt(document.getElementById('limit-pdf-max-extract').value, 10);
    const pdfPaper = parseInt(document.getElementById('limit-pdf-paper-threshold').value, 10);
    const pdfParser = document.getElementById('limit-pdf-parser').value;
    const pdfExApp = document.getElementById('limit-pdf-exclude-appendix').checked;
    const pdfExRef = document.getElementById('limit-pdf-exclude-references').checked;
    const pdfDetectFlaws = document.getElementById('limit-pdf-detect-encoding-flaws').checked;
    const pdfPaperEff = document.getElementById('limit-pdf-paper-effort').value;
    const pdfDefEff = document.getElementById('limit-pdf-default-effort').value;
    const pdfClassEff = document.getElementById('limit-pdf-classifier-effort').value;

    const extBudget = parseInt(document.getElementById('limit-extract-char-budget').value, 10);
    const mergedExtBudget = parseInt(document.getElementById('limit-merged-extract-budget').value, 10);
    const slicingStrat = document.getElementById('limit-slicing-strategy').value;

    const embedBudget = parseInt(document.getElementById('limit-embed-budget').value, 10);
    const expandBudget = parseInt(document.getElementById('limit-expand-budget').value, 10);
    const expandMax = parseInt(document.getElementById('limit-expand-max').value, 10);
    const researchBudget = parseInt(document.getElementById('limit-research-budget').value, 10);

    const presPdfBytes = parseInt(document.getElementById('limit-presentation-pdf-bytes').value, 10);
    const videoMax = parseInt(document.getElementById('limit-video-max-extract').value, 10);
    const videoChunk = parseInt(document.getElementById('limit-video-chunk-sec').value, 10);
    const vlmMax = parseInt(document.getElementById('limit-vlm-max-images').value, 10);

    const payload = {
      limits: {
        raw_char_budget: isNaN(rawBudget) ? 20000 : rawBudget,
        pdf_max_extract_chars: isNaN(pdfMax) ? 50000 : pdfMax,
        pdf_paper_threshold_chars: isNaN(pdfPaper) ? 15000 : pdfPaper,
        pdf_parser: pdfParser,
        pdf_exclude_appendix: pdfExApp,
        pdf_exclude_references: pdfExRef,
        pdf_detect_encoding_flaws: pdfDetectFlaws,
        pdf_paper_effort: pdfPaperEff,
        pdf_default_effort: pdfDefEff,
        pdf_classifier_effort: pdfClassEff,
        extract_char_budget: isNaN(extBudget) ? 20000 : extBudget,
        merged_extract_char_budget: isNaN(mergedExtBudget) ? 0 : mergedExtBudget,
        slicing_strategy: slicingStrat,
        embed_char_budget: isNaN(embedBudget) ? 8000 : embedBudget,
        expand_char_budget: isNaN(expandBudget) ? 2000 : expandBudget,
        expand_max: isNaN(expandMax) ? 5 : expandMax,
        research_context_budget: isNaN(researchBudget) ? 8000 : researchBudget,
        presentation_pdf_max_bytes: isNaN(presPdfBytes) ? 67108864 : presPdfBytes,
        video_max_extract_chars: isNaN(videoMax) ? 200000 : videoMax,
        video_chunk_duration_sec: isNaN(videoChunk) ? 240 : videoChunk,
        vlm_max_images: isNaN(vlmMax) ? 20 : vlmMax
      }
    };

    try {
      const url = resolveRootUrl(_limitState.opts, 'limits');
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        renderLimitManagerDOM('저장 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      const resData = await r.json();
      _limitState.config = resData.config;
      renderLimitManagerDOM('적재 제한 설정이 성공적으로 저장되었습니다 (data/ingest_limits.json 반영 완료).', true);
      if (_limitState.opts && typeof _limitState.opts.onSave === 'function') {
        _limitState.opts.onSave(resData);
      }
    } catch (e) {
      renderLimitManagerDOM('오류 발생: ' + (e.message || e), false);
    }
  }

  async function resetLimitSettings() {
    if (!confirm('적재 제한 설정을 시스템 표준 기본값으로 복원하시겠습니까?')) return;
    const defaults = {
      limits: {
        raw_char_budget: 20000,
        pdf_max_extract_chars: 50000,
        pdf_paper_threshold_chars: 15000,
        pdf_parser: 'default',
        pdf_exclude_appendix: true,
        pdf_exclude_references: true,
        pdf_detect_encoding_flaws: true,
        pdf_paper_effort: 'high',
        pdf_default_effort: '',
        pdf_classifier_effort: 'low',
        extract_char_budget: 20000,
        merged_extract_char_budget: 0,
        slicing_strategy: 'table-exemption',
        embed_char_budget: 8000,
        expand_char_budget: 2000,
        expand_max: 5,
        research_context_budget: 8000,
        presentation_pdf_max_bytes: 67108864,
        video_max_extract_chars: 200000,
        video_chunk_duration_sec: 240,
        vlm_max_images: 20
      }
    };
    try {
      const url = resolveRootUrl(_limitState.opts, 'limits');
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(defaults) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        renderLimitManagerDOM('기본값 복원 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      const resData = await r.json();
      _limitState.config = resData.config;
      renderLimitManagerDOM('적재 제한 설정이 기본값으로 초기화되었습니다.', true);
      if (_limitState.opts && typeof _limitState.opts.onSave === 'function') {
        _limitState.opts.onSave(resData);
      }
    } catch (e) {
      renderLimitManagerDOM('오류 발생: ' + (e.message || e), false);
    }
  }

  function renderLimitManagerDOM(msg, isSuccess) {
    const target = _limitState.container;
    if (!target || !_limitState.config) return;
    const cfg = _limitState.config;
    const lims = cfg.limits || {};

    let alertHtml = '';
    if (msg) {
      const color = isSuccess ? 'var(--accent2,#1a7f37)' : 'var(--err,#cf222e)';
      const bg = isSuccess ? 'rgba(26,127,55,0.1)' : 'rgba(207,34,46,0.1)';
      const icon = isSuccess ? '<span data-icon="check-circle"></span>' : '<span data-icon="warning"></span>';
      alertHtml = `<div style="padding:8px 10px;border-radius:6px;background:${bg};color:${color};font-size:12px;margin-bottom:12px;border:1px solid ${color};">${icon} ${esc(msg)}</div>`;
    }

    let h = '<h2><span data-icon="ruler"></span> 상황 별 적재 제한 관리</h2>';
    h += '<p class="al">수집 경로 및 문서 유형(웹, PDF, 슬라이싱, 벡터 임베딩, 확장, 미디어 등)별 적재 제한과 글자 수 예산을 단독 기능으로 설정합니다. 저장된 설정은 <code>data/ingest_limits.json</code>에 즉시 영구 저장됩니다.</p>';
    h += alertHtml;

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="globe"></span> 일반 웹 / 텍스트 수집 제한</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div>';
    h += '<label style="font-size:11px;opacity:0.8;display:block;">원문 수집 본문 상한 (raw_char_budget, 글자 수)</label>';
    h += `<input id="limit-raw-char-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.raw_char_budget ?? 20000}"/>`;
    h += '<div style="font-size:10px;opacity:0.7;margin-top:2px;">웹, 일반 텍스트 파일, X, 유튜브 수집 시 보관할 최대 본문 분량</div>';
    h += '</div>';
    h += '</div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="doc"></span> PDF 문서 수집 및 논문 판정 제한</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">PDF 최대 추출 분량 (글자 수)</label><input id="limit-pdf-max-extract" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.pdf_max_extract_chars ?? 50000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">논문 판정 기준 분량 (글자 수)</label><input id="limit-pdf-paper-threshold" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.pdf_paper_threshold_chars ?? 15000}"/></div>`;
    h += '</div>';
    h += '<div><label style="font-size:11px;opacity:0.8;">PDF 파서 엔진</label>';
    h += '<select id="limit-pdf-parser" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += '<option value="default" selected>기본 Chromium PDFium 고속 파서</option>';
    h += '</select></div>';
    h += '<div style="display:flex;flex-direction:column;gap:4px;margin-top:4px;">';
    h += `<label style="cursor:pointer;"><input id="limit-pdf-exclude-appendix" type="checkbox" ${lims.pdf_exclude_appendix!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>논문 부록(Appendix) 자동 제외</label>`;
    h += `<label style="cursor:pointer;"><input id="limit-pdf-exclude-references" type="checkbox" ${lims.pdf_exclude_references!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>논문 참고문헌(References) 자동 제외</label>`;
    h += `<label style="cursor:pointer;"><input id="limit-pdf-detect-encoding-flaws" type="checkbox" ${lims.pdf_detect_encoding_flaws!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>텍스트 인코딩 결함 및 스캔본 자동 감지</label>`;
    h += '</div>';
    h += '<div style="display:flex;gap:8px;margin-top:4px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">논문 추론 수준</label>';
    h += '<select id="limit-pdf-paper-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="low" ${lims.pdf_paper_effort==='low'?'selected':''}>low</option>`;
    h += `<option value="medium" ${lims.pdf_paper_effort==='medium'?'selected':''}>medium</option>`;
    h += `<option value="high" ${(lims.pdf_paper_effort||'high')==='high'?'selected':''}>high</option>`;
    h += '</select></div>';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">단문/일반 추론 수준</label>';
    h += '<select id="limit-pdf-default-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="" ${!lims.pdf_default_effort?'selected':''}>기본값(공란)</option>`;
    h += `<option value="low" ${lims.pdf_default_effort==='low'?'selected':''}>low</option>`;
    h += `<option value="medium" ${lims.pdf_default_effort==='medium'?'selected':''}>medium</option>`;
    h += `<option value="high" ${lims.pdf_default_effort==='high'?'selected':''}>high</option>`;
    h += '</select></div>';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">1차 분류 추론 수준</label>';
    h += '<select id="limit-pdf-classifier-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="low" ${(lims.pdf_classifier_effort||'low')==='low'?'selected':''}>low</option>`;
    h += `<option value="medium" ${lims.pdf_classifier_effort==='medium'?'selected':''}>medium</option>`;
    h += `<option value="high" ${lims.pdf_classifier_effort==='high'?'selected':''}>high</option>`;
    h += '</select></div>';
    h += '</div>';
    h += '</div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="puzzle"></span> 지식 그래프(KG) 추출 및 슬라이싱 예산</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">단일 문서 추출 투입 상한 (글자 수)</label><input id="limit-extract-char-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.extract_char_budget ?? 20000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">병합 문서 투입 상한 (0=2배 자동)</label><input id="limit-merged-extract-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.merged_extract_char_budget ?? 0}"/></div>`;
    h += '</div>';
    h += '<div><label style="font-size:11px;opacity:0.8;">본문 슬라이싱 전략</label>';
    h += '<select id="limit-slicing-strategy" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="table-exemption" ${(lims.slicing_strategy||'table-exemption')==='table-exemption'?'selected':''}>표 보존형 (table-exemption, 테이블 데이터 완전 보존)</option>`;
    h += `<option value="strict" ${lims.slicing_strategy==='strict'?'selected':''}>단순 절단형 (strict, 글자 수 한도 엄격 절단)</option>`;
    h += '</select></div>';
    h += '</div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="compass"></span> 임베딩·1홉 확장·리서치 맥락 제한</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">임베딩 벡터 본문 상한 (글자 수)</label><input id="limit-embed-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.embed_char_budget ?? 8000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">심층 리서치 맥락 상한 (글자 수)</label><input id="limit-research-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.research_context_budget ?? 8000}"/></div>`;
    h += '</div>';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">1홉 확장 선별 컨텍스트 (글자 수)</label><input id="limit-expand-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.expand_char_budget ?? 2000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">1홉 확장 최대 링크 수</label><input id="limit-expand-max" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.expand_max ?? 5}"/></div>`;
    h += '</div>';
    h += '</div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="film"></span> 미디어 및 첨부파일 적재 제한</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div>';
    h += '<label style="font-size:11px;opacity:0.8;display:block;">프레젠테이션 PDF 첨부파일 용량 상한 (bytes, 기본 64MiB = 67,108,864)</label>';
    h += `<input id="limit-presentation-pdf-bytes" type="number" min="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.presentation_pdf_max_bytes ?? 67108864}"/>`;
    h += '</div>';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">비디오 STT 최대 추출 분량</label><input id="limit-video-max-extract" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.video_max_extract_chars ?? 200000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">비디오 분할 단위 (초)</label><input id="limit-video-chunk-sec" type="number" min="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.video_chunk_duration_sec ?? 240}"/></div>`;
    h += '</div>';
    h += '<div>';
    h += '<label style="font-size:11px;opacity:0.8;display:block;">VLM 시각 자료 최대 이미지 수</label>';
    h += `<input id="limit-vlm-max-images" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.vlm_max_images ?? 20}"/>`;
    h += '</div>';
    h += '</div>';
    h += '</div>';

    const optsState = _limitState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    h += '<div style="display:flex;gap:8px;margin-top:16px;">';
    h += '<button type="button" class="btn" style="flex:1;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="saveLimitSettings()"><span data-icon="save"></span> 설정 저장</button>';
    h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="resetLimitSettings()"><span data-icon="restore"></span> 기본값 복원</button>';
    if (canClose) {
      h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="closeLimitManager()">닫기</button>';
    }
    h += '</div>';

    target.innerHTML = h;
  }

  async function renderLimitManager(container, opts) {
    if (container instanceof HTMLElement || (container && container.nodeType === 1) || (typeof container === 'string' && (container.startsWith('#') || container.startsWith('.')))) {
      _limitState.container = resolveContainer(container);
      _limitState.opts = opts || {};
    } else if (typeof container === 'string') {
      const msg = container;
      const isSuccess = Boolean(opts);
      renderLimitManagerDOM(msg, isSuccess);
      return;
    }
    const target = _limitState.container;
    if (!target) return;
    target.innerHTML = '<h2><span data-icon="ruler"></span> 적재 제한 관리</h2><p class="hint">설정 로딩 중…</p>';
    try {
      const url = resolveRootUrl(_limitState.opts, 'limits');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _limitState.config = await r.json();
      renderLimitManagerDOM();
    } catch (e) {
      target.innerHTML = `<h2><span data-icon="ruler"></span> 적재 제한 관리</h2><p class="hint" style="color:var(--err,#cf222e)">설정을 불러올 수 없습니다: ${esc(e.message || e)}</p>`;
    }
  }

  // ==========================================
  // 3. THEME MANAGER
  // ==========================================

  function closeThemeManager() {
    if (_themeState.opts && typeof _themeState.opts.onClose === 'function') {
      _themeState.opts.onClose();
    } else if (typeof window.closeDrawer === 'function') {
      window.closeDrawer();
    }
  }

  function toggleThemeOptionsView(themeId) {
    const el = document.getElementById('theme-options-view-' + themeId);
    if (el) {
      el.style.display = (el.style.display === 'none') ? 'flex' : 'none';
    }
  }

  function showThemeEditForm(themeId) {
    const form = document.getElementById('theme-edit-form-' + themeId);
    if (form) {
      form.style.display = form.style.display === 'none' ? 'flex' : 'none';
    }
  }

  function hideThemeEditForm(themeId) {
    const form = document.getElementById('theme-edit-form-' + themeId);
    if (form) {
      form.style.display = 'none';
    }
  }

  async function updateThemeFromUI(themeId) {
    const labelEl = document.getElementById('editthemep-label-' + themeId);
    const descEl = document.getElementById('editthemep-desc-' + themeId);
    const focusEl = document.getElementById('editthemep-focus-' + themeId);
    const iconEl = document.getElementById('editthemep-icon-' + themeId);
    const pubEl = document.getElementById('editthemep-pub-' + themeId);
    const collabEl = document.getElementById('editthemep-collab-' + themeId);
    const presEl = document.getElementById('editthemep-pres-' + themeId);
    const gaEl = document.getElementById('editthemep-ga-' + themeId);
    const sorcererEl = document.getElementById('editthemep-sorcerer-' + themeId);

    const label = ((labelEl || {}).value || '').trim();
    if (!label) { alert('테마 이름을 입력하세요.'); return; }
    const description = ((descEl || {}).value || '').trim();
    const icon = ((iconEl || {}).value || '📁').trim() || '📁';
    const is_public = pubEl ? pubEl.checked : true;

    const tObj = findTheme(themeId);
    const sendId = (themeId === 0 || themeId === '0') ? 0 : (tObj && tObj.id !== undefined && !tObj.number ? tObj.id : String(themeId));
    const payload = { id: sendId, label, description, icon, is_public };
    if (sorcererEl !== null && sorcererEl !== undefined) {
      payload.sorcerer = sorcererEl.value.trim();
    }
    if (collabEl) {
      payload.is_collaborator_accessible = collabEl.checked;
    }
    if (presEl) {
      payload.presentation_enabled = presEl.checked;
    }
    if (focusEl) {
      payload.default_focus = focusEl.value.trim();
    }
    if (gaEl) {
      payload.ga_measurement_id = gaEl.value.trim() || null;
    }

    try {
      const url = resolveApiUrl(_themeState.opts, 'themes');
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert('테마 수정 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
        return;
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChange === 'function') {
        await _themeState.opts.onThemeChange('update', sendId);
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChanged === 'function') {
        await _themeState.opts.onThemeChanged('update', sendId);
      }
      await renderThemeManager(_themeState.container, _themeState.opts);
    } catch (e) {
      alert('오류: ' + e);
    }
  }

  async function toggleThemeVisibility(themeId, newVisibility) {
    try {
      const tObj = findTheme(themeId);
      const sendId = (themeId === 0 || themeId === '0') ? 0 : (tObj && tObj.id !== undefined && !tObj.number ? tObj.id : String(themeId));
      const url = resolveApiUrl(_themeState.opts, 'themes');
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: sendId, is_public: newVisibility }) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert('테마 공개 여부 수정 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
        return;
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChange === 'function') {
        await _themeState.opts.onThemeChange('visibility', sendId);
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChanged === 'function') {
        await _themeState.opts.onThemeChanged('visibility', sendId);
      }
      await renderThemeManager(_themeState.container, _themeState.opts);
    } catch (e) {
      alert('오류: ' + e);
    }
  }

  async function toggleThemeCollaborator(themeId, newAccess) {
    try {
      const tObj = findTheme(themeId);
      const sendId = (themeId === 0 || themeId === '0') ? 0 : (tObj && tObj.id !== undefined && !tObj.number ? tObj.id : String(themeId));
      const url = resolveApiUrl(_themeState.opts, 'themes');
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: sendId, is_collaborator_accessible: newAccess }) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert('협업자 공개 여부 수정 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
        return;
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChange === 'function') {
        await _themeState.opts.onThemeChange('collaborator', sendId);
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChanged === 'function') {
        await _themeState.opts.onThemeChanged('collaborator', sendId);
      }
      await renderThemeManager(_themeState.container, _themeState.opts);
    } catch (e) {
      alert('오류: ' + e);
    }
  }

  async function toggleThemePresentation(themeId, newPres) {
    try {
      const tObj = findTheme(themeId);
      const sendId = (themeId === 0 || themeId === '0') ? 0 : (tObj && tObj.id !== undefined && !tObj.number ? tObj.id : String(themeId));
      const url = resolveApiUrl(_themeState.opts, 'themes');
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: sendId, presentation_enabled: Boolean(newPres) }) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert('프레젠테이션 설정 수정 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
        return;
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChange === 'function') {
        await _themeState.opts.onThemeChange('presentation', sendId);
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChanged === 'function') {
        await _themeState.opts.onThemeChanged('presentation', sendId);
      }
      await renderThemeManager(_themeState.container, _themeState.opts);
    } catch (e) {
      alert('오류: ' + e);
    }
  }

  async function createThemeFromUI() {
    const labelEl = document.getElementById('newthemep-label');
    const descEl = document.getElementById('newthemep-desc');
    const focusEl = document.getElementById('newthemep-focus');
    const iconEl = document.getElementById('newthemep-icon');
    const pubEl = document.getElementById('newthemep-pub');
    const collabEl = document.getElementById('newthemep-collab');
    const presEl = document.getElementById('newthemep-pres');
    const gaEl = document.getElementById('newthemep-ga');
    const sorcererEl = document.getElementById('newthemep-sorcerer');

    const label = ((labelEl || {}).value || '').trim();
    if (!label) { alert('테마 이름을 입력하세요.'); return; }
    const description = ((descEl || {}).value || '').trim();
    const default_focus = ((focusEl || {}).value || '').trim();
    const icon = ((iconEl || {}).value || '📁').trim() || '📁';
    const is_public = pubEl ? pubEl.checked : true;
    const is_collaborator_accessible = collabEl ? collabEl.checked : true;
    const presentation_enabled = presEl ? presEl.checked : false;
    const ga_measurement_id = ((gaEl || {}).value || '').trim() || null;
    const sorcerer = ((sorcererEl || {}).value || '').trim();

    const payload = { label, description, default_focus, icon, is_public, is_collaborator_accessible, presentation_enabled };
    if (sorcerer) payload.sorcerer = sorcerer;
    if (ga_measurement_id) payload.ga_measurement_id = ga_measurement_id;

    try {
      const url = resolveApiUrl(_themeState.opts, 'themes');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert('테마 생성 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
        return;
      }
      const resData = await r.json().catch(() => ({}));
      if (_themeState.opts && typeof _themeState.opts.onThemeChange === 'function') {
        await _themeState.opts.onThemeChange('create', (resData.theme && resData.theme.id) || null);
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChanged === 'function') {
        await _themeState.opts.onThemeChanged('create', (resData.theme && resData.theme.id) || null);
      }
      await renderThemeManager(_themeState.container, _themeState.opts);
    } catch (e) {
      alert('오류: ' + e);
    }
  }

  async function deleteThemeFromUI(themeId, label) {
    const themeStr = String(themeId);
    if (themeStr === '0') {
      alert('기본 지식베이스(기본 테마)는 삭제할 수 없습니다.');
      return;
    }
    const confirmed = confirm(`'${label}' 테마를 완전히 삭제하시겠습니까?\n\n이 테마에 저장된 데이터베이스 및 모든 파일이 영구 삭제(소각)됩니다.`);
    if (!confirmed) return;

    try {
      const url = resolveApiUrl(_themeState.opts, `themes?id=${encodeURIComponent(themeStr)}&purge=1`);
      const r = await fetch(url, { method: 'DELETE' });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert('테마 삭제 실패: ' + (err.detail || err.error || ('HTTP ' + r.status)));
        return;
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChange === 'function') {
        await _themeState.opts.onThemeChange('delete', themeStr);
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChanged === 'function') {
        await _themeState.opts.onThemeChanged('delete', themeStr);
      }
      await renderThemeManager(_themeState.container, _themeState.opts);
    } catch (e) {
      alert('오류: ' + e);
    }
  }

  function renderThemeManagerDOM() {
    const target = _themeState.container;
    if (!target) return;
    const themes = _themeState.themes || [];
    const optsState = _themeState.opts || {};
    const isOwner = evalBool(optsState.isOwner, evalBool(optsState.canWrite, true));
    const isCollab = evalBool(optsState.isCollaborator, false);
    const defaultMgr = optsState.defaultSorcerer || (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.sorcerer) || 'owner';

    let h = '<h2><span data-icon="folder"></span> 지식 테마 관리</h2>';
    if (isOwner) {
      h += '<p class="al">지식 관리자(owner)는 테마별로 공개 여부 및 협업자 접근을 설정할 수 있습니다. 비공개 테마는 익명 사용자에게 노출되지 않습니다.</p>';
    } else {
      h += '<p class="al">협업자(collaborator)는 권한이 부여된 테마의 상세 설정 및 옵션을 확인할 수 있습니다. (테마 수정/생성/삭제는 소유자 전용)</p>';
    }

    h += '<div class="theme-manager-list" style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px;">';
    for (const t of themes) {
      const themeKey = getThemeKey(t);
      const isPub = t.is_public !== false;
      const pubBadge = isPub ? '<span style="color:var(--accent,#00ffaa);font-weight:600;font-size:12px;background:rgba(0,255,170,0.1);padding:2px 6px;border-radius:4px">공개</span>' : '<span style="color:var(--warn,#f39c12);font-weight:600;font-size:12px;background:rgba(243,156,18,0.1);padding:2px 6px;border-radius:4px">비공개</span>';
      const toggleBtn = isPub ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeVisibility(${jsArg(themeKey)}, false)">비공개로 전환</button>` : `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeVisibility(${jsArg(themeKey)}, true)">공개로 전환</button>`;
      const isCollabAcc = t.is_collaborator_accessible !== false;
      const collabBadge = isCollabAcc ? '<span style="color:#60a5fa;font-weight:600;font-size:12px;background:rgba(96,165,250,0.1);padding:2px 6px;border-radius:4px">협업자 공개</span>' : '<span style="color:var(--muted,#888);font-weight:600;font-size:12px;background:rgba(136,136,136,0.1);padding:2px 6px;border-radius:4px">협업자 차단</span>';
      const toggleCollabBtn = isCollabAcc ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeCollaborator(${jsArg(themeKey)}, false)">협업자 차단</button>` : `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeCollaborator(${jsArg(themeKey)}, true)">협업자 공개</button>`;
      const isPres = t.presentation_enabled === true;
      const presBadge = isPres ? '<span style="color:#38bdf8;font-weight:600;font-size:12px;background:rgba(56,189,248,0.12);padding:2px 6px;border-radius:4px" title="프레젠테이션 기능 활성화됨"><span data-icon="screen"></span> 발표 ON</span>' : '<span style="color:var(--muted,#888);font-weight:600;font-size:12px;background:rgba(136,136,136,0.1);padding:2px 6px;border-radius:4px" title="프레젠테이션 기능 비활성화됨"><span data-icon="block"></span> 발표 OFF</span>';
      const togglePresBtn = isPres ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemePresentation(${jsArg(themeKey)}, false)">발표 끄기</button>` : `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemePresentation(${jsArg(themeKey)}, true)">발표 켜기</button>`;
      const canIngestHere = themeKey !== '0' && isCollabAcc;
      const idLabel = themeKey === '0' ? '기본 (ID 0)' : (t.number ? `테마 ${t.number}` : `테마 #${t.id}`);
      const rawMgr = (t.sorcerer || t.knowledge_manager || '').trim();
      const effMgr = rawMgr || (t.effective_sorcerer || defaultMgr);
      const legacyHostsBadge = (t.legacy_hosts && t.legacy_hosts.length) ? `<span style="color:#a78bfa;font-weight:600;font-size:12px;background:rgba(167,139,250,0.15);padding:2px 6px;border-radius:4px" title="구 도메인 리다이렉트"><span data-icon="globe"></span> 구 도메인 ${t.legacy_hosts.length}개</span>` : '';

      h += '<div style="border:1px solid var(--border);border-radius:6px;padding:8px 10px;background:var(--sec-bg);">';
      h += '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;flex-wrap:wrap;">';
      h += `<strong style="font-size:13px;">${esc(t.icon || '📁')} ${esc(t.label)} <small style="opacity:0.7">(${idLabel})</small></strong>`;
      h += '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;">';
      h += legacyHostsBadge;
      if (t.ga_measurement_id) {
        h += `<span style="color:#fbbf24;font-weight:600;font-size:12px;background:rgba(251,191,36,0.15);padding:2px 6px;border-radius:4px" title="Google Analytics 4"><span data-icon="chart"></span> ${esc(t.ga_measurement_id)}</span>`;
      }
      h += pubBadge;
      if (isOwner) h += toggleBtn;
      if (themeKey !== '0') h += collabBadge;
      if (isOwner && themeKey !== '0') h += toggleCollabBtn;
      h += presBadge;
      if (isOwner) h += togglePresBtn;
      if (isOwner) h += `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="showThemeEditForm(${jsArg(themeKey)})"><span data-icon="edit"></span> 수정</button>`;
      h += `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeOptionsView(${jsArg(themeKey)})"><span data-icon="settings"></span> 옵션 보기</button>`;
      if (isOwner && themeKey !== '0') {
        h += `<button type="button" class="sec danger-btn" style="font-size:11px;padding:3px 8px;color:var(--err,#cf222e);border-color:rgba(207,34,46,0.3);" onclick="deleteThemeFromUI(${jsArg(themeKey)}, ${jsArg(t.label)})"><span data-icon="trash"></span> 삭제</button>`;
      }
      h += '</div></div>';
      if (t.description) h += `<p style="margin:4px 0 0;font-size:12px;opacity:0.8">${esc(t.description)}</p>`;
      h += `<p style="margin:4px 0 0;font-size:12px;opacity:0.8">지식 관리자: ${esc(effMgr)}</p>`;
      if (themeKey !== '0' && t.default_focus) {
        h += `<p style="margin:4px 0 0;font-size:12px;color:var(--accent,#00ffaa);font-weight:500;"><span data-icon="target"></span> 기본 초점: ${esc(t.default_focus)}</p>`;
      }

      h += `<div id="theme-options-view-${themeKey}" style="display:${isCollab ? 'flex' : 'none'};margin-top:10px;padding:8px 10px;border-top:1px dashed var(--border);background:var(--panel-bg);border-radius:4px;font-size:12px;flex-direction:column;gap:6px;">`;
      h += '<div style="font-weight:600;color:var(--accent2);margin-bottom:2px;"><span data-icon="settings"></span> 테마 설정 옵션</div>';
      h += `<div>• <b>레이블(이름)</b>: ${esc(t.label)}</div>`;
      h += `<div>• <b>지식 관리자</b>: ${esc(effMgr)}</div>`;
      h += `<div>• <b>아이콘</b>: ${esc(t.icon || '📁')}</div>`;
      h += `<div>• <b>설명</b>: ${esc(t.description || '(설명 없음)')}</div>`;
      h += `<div>• <b>구 도메인 리다이렉트 (legacy_hosts)</b>: ${(t.legacy_hosts && t.legacy_hosts.length) ? t.legacy_hosts.map(lh => `${esc(lh.host)} (활성: ${esc(lh.activated_at || '-')}, 일몰: ${esc(lh.sunset_at || '-')})`).join(', ') : '(없음)'}</div>`;
      h += `<div>• <b>GA4 측정 ID</b>: ${esc(t.ga_measurement_id || '(미지정 - 비활성)')}</div>`;
      if (themeKey !== '0') h += `<div>• <b>기본 초점</b>: ${esc(t.default_focus || '(미지정 - 기본 분석 적용)')}</div>`;
      h += `<div>• <b>공개 여부</b>: ${isPub ? '익명 사용자 공개' : '비공개'}</div>`;
      if (themeKey !== '0') h += `<div>• <b>협업자 공개 여부</b>: ${isCollabAcc ? '협업자 공개 (접근 및 적재 허용)' : '협업자 차단'}</div>`;
      h += `<div>• <b>협업자 적재 권한</b>: ${canIngestHere ? '<span style="color:var(--accent,#00ffaa);font-weight:600;">허용됨 (자료 적재 가능)</span>' : (themeKey === '0' ? '<span style="color:var(--muted);font-weight:600;">제한됨 (소유자 기본 지식베이스)</span>' : '<span style="color:var(--warn);font-weight:600;">제한됨 (소유자 미허용)</span>')}</div>`;
      h += `<div>• <b>프레젠테이션 기능</b>: ${isPres ? '<span style="color:#38bdf8;font-weight:600;">활성화됨 (발표 기능 사용 가능)</span>' : '<span style="color:var(--muted);font-weight:600;">비활성화됨 (발표 기능 숨김)</span>'}</div>`;
      if (t.stats) h += `<div>• <b>지식 현황</b>: 문서 ${t.stats.documents || 0}건 · 엔티티 ${t.stats.entities || 0}개 · 관계 ${t.stats.relations || 0}개</div>`;
      h += '</div>';

      if (isOwner) {
        h += `<div id="theme-edit-form-${themeKey}" style="display:none;margin-top:10px;padding-top:8px;border-top:1px dashed var(--border);flex-direction:column;gap:8px;">`;
        h += `<div><label style="font-size:11px;opacity:0.8">레이블(이름)</label><input id="editthemep-label-${themeKey}" style="width:100%;box-sizing:border-box" value="${esc(t.label)}"/></div>`;
        h += `<div><label style="font-size:11px;opacity:0.8">지식 관리자</label><input id="editthemep-sorcerer-${themeKey}" style="width:100%;box-sizing:border-box" placeholder="지식 관리자" value="${esc(rawMgr)}"/></div>`;
        h += `<div><label style="font-size:11px;opacity:0.8">설명</label><input id="editthemep-desc-${themeKey}" style="width:100%;box-sizing:border-box" value="${esc(t.description || '')}"/></div>`;
        if (t.legacy_hosts && t.legacy_hosts.length) {
          h += `<div style="font-size:11px;color:var(--muted);padding:2px 0;"><span data-icon="globe"></span> 구 도메인(FQDN)은 폐지되었으며 읽기 전용 리다이렉트로 관리됩니다: ${t.legacy_hosts.map(lh => esc(lh.host)).join(', ')}</div>`;
        }
        h += `<div><label style="font-size:11px;opacity:0.8">Google Analytics 4 측정 ID (예: G-XXXXXXXXXX)</label><input id="editthemep-ga-${themeKey}" style="width:100%;box-sizing:border-box" placeholder="예: G-XXXXXXXXXX" value="${esc(t.ga_measurement_id || '')}"/></div>`;
        if (themeKey !== '0') {
          h += `<div><label style="font-size:11px;opacity:0.8">기본 초점 (적재 시 기본 적용할 방향/지침)</label><input id="editthemep-focus-${themeKey}" style="width:100%;box-sizing:border-box" placeholder="예: 시스템 아키텍처 및 핵심 API 사양 중심" value="${esc(t.default_focus || '')}"/></div>`;
        }
        h += '<div style="display:flex;gap:8px;flex-wrap:wrap;">';
        h += `<div style="flex:1;min-width:80px;"><label style="font-size:11px;opacity:0.8">아이콘</label><input id="editthemep-icon-${themeKey}" style="width:100%;box-sizing:border-box" value="${esc(t.icon || '📁')}"/></div>`;
        h += `<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="editthemep-pub-${themeKey}" type="checkbox" ${isPub ? 'checked' : ''} style="width:auto;margin-right:4px;vertical-align:middle;"/>익명 사용자에게 공개</label></div>`;
        if (themeKey !== '0') {
          h += `<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="editthemep-collab-${themeKey}" type="checkbox" ${isCollabAcc ? 'checked' : ''} style="width:auto;margin-right:4px;vertical-align:middle;"/>협업자에게 공개</label></div>`;
        }
        h += `<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="editthemep-pres-${themeKey}" type="checkbox" ${isPres ? 'checked' : ''} style="width:auto;margin-right:4px;vertical-align:middle;"/>프레젠테이션 기능 활성화</label></div>`;
        h += '</div>';
        h += '<div style="display:flex;gap:6px;margin-top:4px;align-items:center;">';
        h += `<button type="button" style="padding:4px 12px;" onclick="updateThemeFromUI(${jsArg(themeKey)})">저장</button>`;
        h += `<button type="button" class="sec" style="padding:4px 12px;" onclick="hideThemeEditForm(${jsArg(themeKey)})">취소</button>`;
        if (themeKey !== '0') {
          h += `<button type="button" class="sec danger-btn" style="margin-left:auto;padding:4px 10px;font-size:11px;color:var(--err,#cf222e);border-color:rgba(207,34,46,0.3);" onclick="deleteThemeFromUI(${jsArg(themeKey)}, ${jsArg(t.label)})"><span data-icon="trash"></span> 테마 완전 삭제</button>`;
        }
        h += '</div></div>';
      }
      h += '</div>';
    }
    h += '</div>';

    if (isOwner) {
      h += '<details style="border:1px solid var(--border);border-radius:6px;padding:8px 10px;background:var(--sec-bg);"><summary style="cursor:pointer;font-weight:600;font-size:13px;"><span data-icon="plus"></span> 새 테마 추가</summary>';
      h += '<div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">';
      h += '<div><label style="font-size:11px;opacity:0.8">레이블(이름)</label><input id="newthemep-label" style="width:100%;box-sizing:border-box" placeholder="예: 경제 및 금융"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">지식 관리자</label><input id="newthemep-sorcerer" style="width:100%;box-sizing:border-box" placeholder="지식 관리자"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">설명</label><input id="newthemep-desc" style="width:100%;box-sizing:border-box" placeholder="예: 거시경제 및 시장 분석"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">Google Analytics 4 측정 ID (예: G-XXXXXXXXXX)</label><input id="newthemep-ga" style="width:100%;box-sizing:border-box" placeholder="예: G-XXXXXXXXXX"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">기본 초점 (적재 시 기본 적용할 방향/지침)</label><input id="newthemep-focus" style="width:100%;box-sizing:border-box" placeholder="예: 거시경제 지표 및 시장 영향 중심"/></div>';
      h += '<div style="display:flex;gap:8px;flex-wrap:wrap;"><div style="flex:1;min-width:80px;"><label style="font-size:11px;opacity:0.8">아이콘</label><input id="newthemep-icon" style="width:100%;box-sizing:border-box" value="📁"/></div>';
      h += '<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="newthemep-pub" type="checkbox" checked style="width:auto;margin-right:4px;vertical-align:middle;"/>익명 사용자에게 공개</label></div>';
      h += '<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="newthemep-collab" type="checkbox" checked style="width:auto;margin-right:4px;vertical-align:middle;"/>협업자에게 공개</label></div>';
      h += '<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="newthemep-pres" type="checkbox" style="width:auto;margin-right:4px;vertical-align:middle;"/>프레젠테이션 기능 활성화</label></div></div>';
      h += '<button type="button" style="align-self:flex-start;padding:4px 12px;margin-top:4px;" onclick="createThemeFromUI()">테마 생성</button>';
      h += '</div></details>';
    } else {
      h += '<p class="hint" style="margin-top:14px;font-size:12px;"><span data-icon="info"></span> 새로운 지식 테마 생성 또는 설정 변경이 필요한 경우 지식 관리자(owner)에게 요청하세요.</p>';
    }

    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    if (canClose) {
      h += '<div style="margin-top:16px;">';
      h += '<button type="button" class="sec" style="padding:8px 12px;width:100%;" onclick="closeThemeManager()">닫기</button>';
      h += '</div>';
    }

    target.innerHTML = h;
  }

  async function renderThemeManager(container, opts) {
    if (container instanceof HTMLElement || (container && container.nodeType === 1) || (typeof container === 'string' && (container.startsWith('#') || container.startsWith('.')))) {
      _themeState.container = resolveContainer(container);
      _themeState.opts = opts || {};
    }
    const target = _themeState.container;
    if (!target) return;
    target.innerHTML = '<h2><span data-icon="folder"></span> 지식 테마 관리</h2><p class="hint">테마 목록 조회 중…</p>';
    try {
      const url = resolveApiUrl(_themeState.opts, 'themes');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      _themeState.themes = data.themes || [];
      renderThemeManagerDOM();
    } catch (e) {
      target.innerHTML = '<p class="hint">테마 목록 조회 실패: ' + esc(String(e)) + '</p>';
    }
  }

  // ==========================================
  // 4. TOKEN MANAGER
  // ==========================================

  function closeTokenManager() {
    if (_tokenState.opts && typeof _tokenState.opts.onClose === 'function') {
      _tokenState.opts.onClose();
    } else if (typeof window.closeDrawer === 'function') {
      window.closeDrawer();
    }
  }

  function copyMintedToken() {
    const el = document.getElementById('newly-minted-token');
    if (!el) return;
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      navigator.clipboard.writeText(el.value).then(() => {
        alert('토큰이 클립보드에 복사되었습니다.');
      }).catch(() => {
        el.select();
        document.execCommand('copy');
        alert('토큰이 클립보드에 복사되었습니다.');
      });
    } else {
      el.select();
      document.execCommand('copy');
      alert('토큰이 클립보드에 복사되었습니다.');
    }
  }

  async function createApiToken() {
    const nameInput = document.getElementById('new-token-name');
    const scopeSelect = document.getElementById('new-token-scope');
    const expirySelect = document.getElementById('new-token-expiry');
    if (!nameInput || !nameInput.value.trim()) {
      alert('토큰 이름을 입력하세요.');
      return;
    }
    const name = nameInput.value.trim();
    const scope = scopeSelect ? scopeSelect.value : 'readonly';
    const expDays = expirySelect ? parseInt(expirySelect.value, 10) : 0;

    try {
      const url = resolveRootUrl(_tokenState.opts, 'tokens');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name, scope: scope, expires_in_days: expDays > 0 ? expDays : null }) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        renderTokenManagerDOM('발급 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      const data = await r.json();
      _tokenState.newCreatedToken = data.token;
      if (_tokenState.opts && typeof _tokenState.opts.onTokenCreated === 'function') {
        _tokenState.opts.onTokenCreated(data.token);
      }
      if (_tokenState.opts && typeof _tokenState.opts.onTokenChange === 'function') {
        _tokenState.opts.onTokenChange('create', data.token);
      }
      await renderTokenManager(_tokenState.container, _tokenState.opts, data.token);
    } catch (e) {
      renderTokenManagerDOM('오류 발생: ' + (e.message || e), false);
    }
  }

  async function revokeApiToken(id, name) {
    if (!confirm(`정말 #${id} '${name}' 토큰을 폐기하시겠습니까? 즉시 모든 API 및 MCP 연결이 차단됩니다.`)) return;
    try {
      const url = resolveRootUrl(_tokenState.opts, 'tokens/revoke');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id }) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        renderTokenManagerDOM('폐기 실패: ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      if (_tokenState.opts && typeof _tokenState.opts.onTokenRevoked === 'function') {
        _tokenState.opts.onTokenRevoked(id);
      }
      if (_tokenState.opts && typeof _tokenState.opts.onTokenChange === 'function') {
        _tokenState.opts.onTokenChange('revoke', id);
      }
      await renderTokenManager(_tokenState.container, _tokenState.opts);
      renderTokenManagerDOM(`토큰 #${id} '${name}'이(가) 즉시 폐기되었습니다.`, true);
    } catch (e) {
      renderTokenManagerDOM('오류 발생: ' + (e.message || e), false);
    }
  }

  function renderTokenManagerDOM(msg, isSuccess, newCreatedToken) {
    const target = _tokenState.container;
    if (!target) return;
    const tokens = _tokenState.tokens || [];
    const activeNewToken = newCreatedToken || _tokenState.newCreatedToken;

    let alertHtml = '';
    if (msg) {
      const color = isSuccess ? 'var(--accent2,#1a7f37)' : 'var(--err,#cf222e)';
      const bg = isSuccess ? 'rgba(26,127,55,0.1)' : 'rgba(207,34,46,0.1)';
      const icon = isSuccess ? '<span data-icon="check-circle"></span>' : '<span data-icon="warning"></span>';
      alertHtml = `<div style="padding:8px 10px;border-radius:6px;background:${bg};color:${color};font-size:12px;margin-bottom:12px;border:1px solid ${color};">${icon} ${esc(msg)}</div>`;
    }

    let h = '<h2><span data-icon="key"></span> API &amp; MCP 상시 이용 키 관리</h2>';
    h += '<p class="al">Claude Desktop, Cursor, 백그라운드 데몬 및 외부 API 연동을 위한 상시 이용(24/7) 토큰을 관리합니다. 환경변수 의존 없이 즉시 생성 및 폐기됩니다.</p>';
    h += alertHtml;

    if (activeNewToken) {
      const rawVal = activeNewToken.token || '';
      const mcpOrigin = window.location.origin;
      const mcpUrl = `${mcpOrigin}/mcp`;
      const mcpSnippet = JSON.stringify({ mcpServers: { 'claire-bible': { command: 'npx', args: ['-y', '@modelcontextprotocol/server-everything'], env: { CLAIRE_URL: mcpUrl, CLAIRE_TOKEN: rawVal } } } }, null, 2);
      h += '<div style="background:rgba(26,127,55,0.08);border:2px solid var(--accent2,#1a7f37);border-radius:8px;padding:12px;margin-bottom:16px;">';
      h += '<h3 style="margin:0 0 6px 0;font-size:14px;color:var(--accent2,#1a7f37);"><span data-icon="check-circle"></span> 새 토큰이 발급되었습니다</h3>';
      h += '<p style="margin:0 0 8px 0;font-size:12px;">이 토큰은 다시 전체 문자열로 표시되지 않습니다. 지금 복사하여 보관하세요.</p>';
      h += '<div style="display:flex;gap:6px;margin-bottom:8px;">';
      h += `<input type="text" id="newly-minted-token" readonly value="${esc(rawVal)}" style="flex:1;font-family:monospace;padding:6px 8px;font-size:12px;border:1px solid var(--border);border-radius:4px;background:var(--bg);" onclick="this.select();"/>`;
      h += '<button type="button" class="btn" style="padding:6px 10px;font-size:12px;background:var(--btn-bg,#1f883d);color:#fff;border:none;border-radius:4px;cursor:pointer;" onclick="copyMintedToken()"><span data-icon="clipboard"></span> 복사</button>';
      h += '</div>';
      h += '<details style="font-size:12px;color:var(--fg);"><summary style="cursor:pointer;font-weight:600;margin-bottom:4px;"><span data-icon="clipboard"></span> Claude Desktop / Cursor MCP 연동 JSON 보기</summary>';
      h += `<pre style="background:var(--bg);padding:8px;border-radius:4px;overflow-x:auto;font-size:11px;border:1px solid var(--border);">${esc(mcpSnippet)}</pre></details>`;
      h += '</div>';
    }

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:12px;background:var(--sec-bg);margin-bottom:16px;">';
    h += '<h3 style="margin:0 0 10px 0;font-size:13px;"><span data-icon="plus"></span> 새 API &amp; MCP 토큰 발급</h3>';
    h += '<div style="display:flex;flex-direction:column;gap:10px;">';
    h += '<div>';
    h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">토큰 이름 (용도 / 클라이언트 명칭)</label>';
    h += '<input type="text" id="new-token-name" placeholder="예: Claude Desktop, Cursor Agent, Backup Script" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:13px;"/>';
    h += '</div>';

    h += '<div style="display:flex;gap:12px;flex-wrap:wrap;">';
    h += '<div style="flex:1;min-width:130px;">';
    h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">권한 스코프 (Scope)</label>';
    h += '<select id="new-token-scope" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;">';
    h += '<option value="readonly" selected>읽기 전용 (readonly - MCP 조회 및 검색)</option>';
    h += '<option value="collaborator">협력자 (collaborator - 추가 테마 적재)</option>';
    h += '<option value="owner">소유자 (owner - 전체 읽기/쓰기/관리)</option>';
    h += '</select>';
    h += '</div>';

    h += '<div style="flex:1;min-width:130px;">';
    h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">유효기간 (Expiration)</label>';
    h += '<select id="new-token-expiry" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;">';
    h += '<option value="0" selected>무기한 (상시 이용 가능)</option>';
    h += '<option value="30">30일</option>';
    h += '<option value="90">90일</option>';
    h += '<option value="365">1년 (365일)</option>';
    h += '</select>';
    h += '</div>';
    h += '</div>';

    h += '<button type="button" class="btn" style="margin-top:4px;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="createApiToken()"><span data-icon="key"></span> 토큰 발급</button>';
    h += '</div>';
    h += '</div>';

    h += '<h3 style="margin:0 0 8px 0;font-size:13px;"><span data-icon="clipboard"></span> 발급된 토큰 목록 (' + tokens.length + '개)</h3>';
    if (!tokens.length) {
      h += '<p class="hint">등록된 토큰이 없습니다.</p>';
    } else {
      h += '<div style="display:flex;flex-direction:column;gap:8px;">';
      for (const tok of tokens) {
        const isRevoked = tok.revoked_at !== null && tok.revoked_at !== undefined;
        const nowTs = Date.now() / 1000;
        const isExpired = tok.expires_at && tok.expires_at < nowTs;
        let statusBadge = '<span style="color:#1a7f37;background:rgba(26,127,55,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">활성</span>';
        if (isRevoked) {
          statusBadge = '<span style="color:#cf222e;background:rgba(207,34,46,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">폐기됨</span>';
        } else if (isExpired) {
          statusBadge = '<span style="color:#9a6700;background:rgba(154,103,0,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">만료됨</span>';
        }

        let expStr = '무기한 (상시)';
        if (tok.expires_at) {
          const d = new Date(tok.expires_at * 1000);
          expStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        }

        let lastUsedStr = '사용 기록 없음';
        if (tok.last_used_at) {
          const lu = new Date(tok.last_used_at * 1000);
          lastUsedStr = lu.toLocaleDateString() + ' ' + lu.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        }

        const scopeColors = { owner: 'var(--accent,#0969da)', collaborator: '#8250df', readonly: '#57606a' };
        const scopeCol = scopeColors[tok.scope] || '#57606a';

        h += `<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--bg);opacity:${isRevoked ? '0.6' : '1'};">`;
        h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">';
        h += `<div><strong style="font-size:13px;">#${tok.id} ${esc(tok.name)}</strong> <span style="font-size:11px;font-weight:600;color:${scopeCol};border:1px solid ${scopeCol};padding:1px 5px;border-radius:4px;margin-left:4px;">${esc(tok.scope)}</span> ${statusBadge}</div>`;
        if (!isRevoked) {
          h += `<button type="button" class="sec" style="font-size:11px;padding:2px 8px;color:var(--err,#cf222e);border-color:var(--err,#cf222e);" onclick="revokeApiToken(${tok.id}, ${jsArg(tok.name)})">폐기</button>`;
        }
        h += '</div>';

        h += '<div style="font-size:12px;color:var(--sec-fg,#57606a);display:flex;flex-direction:column;gap:2px;">';
        h += `<div>식별자: <code style="font-size:11px;background:var(--sec-bg);padding:1px 4px;border-radius:3px;">${esc(tok.token_masked || 'cb_...')}</code></div>`;
        h += `<div>만료: ${esc(expStr)} · 최근 사용: ${esc(lastUsedStr)}</div>`;
        h += '</div>';
        h += '</div>';
      }
      h += '</div>';
    }

    const optsState = _tokenState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    if (canClose) {
      h += '<div style="margin-top:16px;">';
      h += '<button type="button" class="sec" style="padding:8px 12px;width:100%;" onclick="closeTokenManager()">닫기</button>';
      h += '</div>';
    }

    target.innerHTML = h;
  }

  async function renderTokenManager(container, opts, newCreatedToken) {
    if (container instanceof HTMLElement || (container && container.nodeType === 1) || (typeof container === 'string' && (container.startsWith('#') || container.startsWith('.')))) {
      _tokenState.container = resolveContainer(container);
      _tokenState.opts = opts || {};
      if (newCreatedToken) _tokenState.newCreatedToken = newCreatedToken;
      else if (_tokenState.opts && _tokenState.opts.initialNewToken) _tokenState.newCreatedToken = _tokenState.opts.initialNewToken;
    } else if (typeof container === 'string') {
      const msg = container;
      const isSuccess = Boolean(opts);
      if (newCreatedToken) _tokenState.newCreatedToken = newCreatedToken;
      renderTokenManagerDOM(msg, isSuccess, _tokenState.newCreatedToken);
      return;
    }
    const target = _tokenState.container;
    if (!target) return;
    target.innerHTML = '<h2><span data-icon="key"></span> API &amp; MCP 상시 이용 키 관리</h2><p class="hint">토큰 목록 조회 중…</p>';
    try {
      const url = resolveRootUrl(_tokenState.opts, 'tokens');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      _tokenState.tokens = data.tokens || [];
      const initialMsg = (_tokenState.opts && _tokenState.opts.initialMsg) || undefined;
      const initialSuccess = (_tokenState.opts && _tokenState.opts.initialSuccess) || false;
      renderTokenManagerDOM(initialMsg, initialSuccess, _tokenState.newCreatedToken);
    } catch (e) {
      target.innerHTML = `<h2><span data-icon="key"></span> API &amp; MCP 상시 이용 키 관리</h2><p class="hint" style="color:var(--err,#cf222e)">토큰 목록을 불러올 수 없습니다: ${esc(e.message || e)}</p>`;
    }
  }

  // ==========================================
  // EXPORTS
  // ==========================================

  const ClaireManagePanels = {
    renderProviderManager,
    renderLimitManager,
    renderThemeManager,
    renderTokenManager,
    togglePassVisibility,
    clearApiKeyInput,
    testProviderConnection,
    saveProviderSettings,
    closeProviderManager,
    saveLimitSettings,
    resetLimitSettings,
    closeLimitManager,
    toggleThemeOptionsView,
    showThemeEditForm,
    hideThemeEditForm,
    updateThemeFromUI,
    toggleThemeVisibility,
    toggleThemeCollaborator,
    toggleThemePresentation,
    createThemeFromUI,
    deleteThemeFromUI,
    closeThemeManager,
    createApiToken,
    revokeApiToken,
    closeTokenManager,
    copyMintedToken
  };

  window.ClaireManagePanels = ClaireManagePanels;
  Object.assign(window, ClaireManagePanels);
})();
