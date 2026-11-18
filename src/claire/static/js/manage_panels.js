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
    if (t.is_personal || String(t.id) === '0' || t.number === '0') return '0';
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
      return themes.find(t => t.is_personal || String(t.id) === '0' || t.number === '0') || themes[0] || null;
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
      el.placeholder = trKey("console.js.panels.providers.key_placeholder_empty");
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
        optionsHtml += `<option value="${esc(cur)}" selected disabled>${esc(cur)} (${esc(trKey("console.js.panels.providers.model_unverified"))})</option>`;
      }
      optionsHtml += `<option value="" ${cur === '' ? 'selected ' : ''}disabled>${esc(trKey("console.js.panels.providers.models_unavailable"))}</option>`;
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
      optionsHtml += `<option value="${esc(cur)}" selected>${esc(cur)} (${esc(trKey("console.js.panels.providers.model_replace"))})</option>`;
    }
    for (const m of items) {
      const text = m.id === '' ? (m.label || trKey("console.js.panels.providers.account_default")) : m.id;
      optionsHtml += `<option value="${esc(m.id)}" ${selected === m.id ? 'selected' : ''}>${esc(text)}</option>`;
    }
    return { optionsHtml, selected, unsupportedCurrent, locked: false, previousVal: cur };
  }

  function renderModelSelectField(id, rawList, fallbackList, currentVal, defaultId) {
    const res = buildModelSelectResult(rawList, fallbackList, currentVal, defaultId);
    const disabledAttr = res.locked ? ' disabled' : '';
    let html = `<select id="${esc(id)}"${disabledAttr} style="width:100%;box-sizing:border-box;margin-top:2px;">${res.optionsHtml}</select>`;
    if (res.unsupportedCurrent) {
      html += `<div style="margin-top:3px;font-size:10px;color:var(--err,#cf222e);">${esc(trKey("console.js.panels.providers.model_unsupported", { model: res.previousVal }))}</div>`;
    }
    return html;
  }

  let _capAbortCtrl = null;
  const PROVIDER_LABELS = {
    gemini: trKey("console.js.panels.providers.gemini_label"),
    mock: trKey("console.js.panels.providers.mock_label")
  };

  function collectProviderFormPayload() {
    const activeProv = document.getElementById('prov-active-select').value;
    const gemKey = document.getElementById('prov-gemini-key');
    const gemModel = document.getElementById('prov-gemini-model').value.trim();
    const gemEffort = document.getElementById('prov-gemini-effort').value;
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
    const slotNames = ['extract_graph', 'summarize', 'entity_judge', 'relation_judge', 'classify', 'research', 'stt', 'vlm'];
    for (const slot of slotNames) {
      const pEl = document.getElementById(`prov-route-${slot}-provider`);
      const mEl = document.getElementById(`prov-route-${slot}-model`);
      const eEl = document.getElementById(`prov-route-${slot}-effort`);
      if (pEl || mEl || eEl) {
        featureRouting[slot] = {
          provider: pEl ? String(pEl.value || 'inherit') : 'inherit',
          model: mEl ? String(mEl.value || '').trim() : '',
          effort: eEl ? String(eEl.value || '').trim().toLowerCase() : ''
        };
      }
    }

    const payload = {
      active_provider: activeProv,
      feature_routing: featureRouting,
      providers: {
        gemini: { model: gemModel, effort: gemEffort, min_interval: gemInterval, max_retries: gemRetries },
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
      return `<span style="display:inline-block;font-size:10px;padding:1px 6px;border-radius:4px;background:${bg};color:${color};margin-right:4px;margin-bottom:3px;" title="${esc(note || '')}">${ok ? esc(trKey("console.js.panels.providers.supported")) : esc(trKey("console.js.panels.providers.unsupported"))} · ${esc(label)}</span>`;
    };
    let out = '<div style="margin:4px 0 6px;">';
    out += chip(trKey("console.js.panels.providers.cap_extract"), Boolean(f.extract));
    out += chip(trKey("console.js.panels.providers.cap_research"), Boolean(f.research));
    out += chip(trKey("console.js.panels.providers.cap_embedding"), Boolean(f.embedding), cap.embedding_note || '');
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
          opt.textContent = `${baseLabel} [${trKey("console.js.panels.providers.locked_option")}]`;
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
          lockEl.textContent = trKey("console.js.panels.providers.locked_prefix") + ' ' + cap.locked_reason;
        } else if (cap.embedding_note) {
          lockEl.style.display = 'block';
          lockEl.style.color = 'var(--warn,#9a6700)';
          lockEl.style.background = 'rgba(154,103,0,0.08)';
          lockEl.style.borderColor = 'rgba(154,103,0,0.35)';
          lockEl.textContent = trKey("console.js.panels.providers.advisory") + ' ' + cap.embedding_note;
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
        tierModelSel.innerHTML = '<option value="">' + esc(trKey("console.js.panels.providers.tier_none_option")) + '</option>' + resT.optionsHtml;
        if (tierModelSel.querySelector(`option[value="${CSS.escape(resT.selected)}"]`)) {
          tierModelSel.value = resT.selected;
        }
      }
    }

    const sttEl = document.getElementById('prov-cap-stt');
    if (sttEl && caps.stt) {
      if (caps.stt.enabled && caps.stt.provider === 'gemini' && !caps.stt.ready) {
        sttEl.style.display = 'block';
        sttEl.textContent = trKey("console.js.panels.providers.stt_warning");
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
        sumEl.innerHTML = '<strong>' + esc(trKey("console.js.panels.providers.save_locked")) + '</strong><ul style="margin:4px 0 0;padding-left:18px;">' + locks.map(r => '<li>' + esc(r) + '</li>').join('') + '</ul>';
      } else if (warns.length > 0) {
        sumEl.style.display = 'block';
        sumEl.style.color = 'var(--warn,#9a6700)';
        sumEl.style.background = 'rgba(154,103,0,0.08)';
        sumEl.style.borderColor = 'rgba(154,103,0,0.35)';
        sumEl.innerHTML = '<strong>' + esc(trKey("console.js.panels.providers.advisory")) + '</strong><ul style="margin:4px 0 0;padding-left:18px;">' + warns.map(w => '<li>' + esc(w) + '</li>').join('') + '</ul>';
      } else {
        sumEl.style.display = 'block';
        sumEl.style.color = 'var(--accent2,#1a7f37)';
        sumEl.style.background = 'rgba(26,127,55,0.08)';
        sumEl.style.borderColor = 'rgba(26,127,55,0.35)';
        sumEl.textContent = trKey("console.js.panels.providers.check_passed");
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
      sumEl.textContent = trKey("console.js.panels.providers.check_in_work");
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
        sumEl.textContent = trKey("console.js.panels.providers.check_failed") + ' ' + (e.message || e);
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
    const slotNames = ['extract_graph', 'summarize', 'entity_judge', 'relation_judge', 'classify', 'research', 'stt', 'vlm'];
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
      resEl.textContent = trKey("console.js.panels.providers.test_in_work");
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
          resEl.textContent = '' + (d.message || trKey("console.js.panels.providers.test_ok"));
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
            for (const sl of ['extract_graph', 'summarize', 'entity_judge', 'relation_judge', 'classify', 'research', 'stt', 'vlm']) {
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
          resEl.textContent = '' + (d.error || trKey("console.js.panels.providers.test_failed"));
        }
      }
      await runProviderCapabilityCheck({ live: provName === 'gemini', refreshAgy: provName === 'antigravity' });
    } catch (e) {
      if (resEl) {
        resEl.style.color = 'var(--err,#cf222e)';
        resEl.textContent = trKey("console.js.panels.comm_error") + ' ' + (e.message || e);
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
        renderProviderManagerDOM(trKey("console.js.panels.save_failed") + ' ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      const resData = await r.json();
      _provState.config = resData.config;
      renderProviderManagerDOM(trKey("console.js.panels.providers.saved"), true);
      if (_provState.opts && typeof _provState.opts.onSave === 'function') {
        _provState.opts.onSave(resData);
      }
    } catch (e) {
      renderProviderManagerDOM(trKey("console.js.panels.error_occurred") + ' ' + (e.message || e), false);
    }
  }

  const FEATURE_SLOT_DEFS = [
    { slot: 'extract_graph', label: trKey("console.js.panels.slot.extract_graph"), note: trKey("console.js.panels.slot.extract_graph_note") },
    { slot: 'summarize', label: trKey("console.js.panels.slot.summarize"), note: trKey("console.js.panels.slot.summarize_note") },
    { slot: 'entity_judge', label: trKey("console.js.panels.slot.entity_judge"), note: trKey("console.js.panels.slot.entity_judge_note") },
    { slot: 'relation_judge', label: trKey("console.js.panels.slot.relation_judge"), note: trKey("console.js.panels.slot.relation_judge_note") },
    { slot: 'classify', label: trKey("console.js.panels.slot.classify"), note: trKey("console.js.panels.slot.classify_note") },
    { slot: 'research', label: trKey("console.js.panels.slot.research"), note: trKey("console.js.panels.slot.research_note") },
    { slot: 'stt', label: trKey("console.js.panels.slot.stt"), note: trKey("console.js.panels.slot.stt_note") },
    { slot: 'vlm', label: trKey("console.js.panels.slot.vlm"), note: trKey("console.js.panels.slot.vlm_note") }
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
    if (effProv === 'gemini') {
      const list = normalizeModelOptions((provs.gemini || {}).available_models, ['gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-4-hydrogen', 'gemini-4-argon', 'gemini-3.1-pro-preview', 'gemini-2.5-flash']);
      return [{ id: '', label: trKey("console.js.panels.routing.inherit_model") }, ...list];
    }
    if (effProv === 'antigravity') {
      const list = normalizeModelOptions((provs.antigravity || {}).available_models, []);
      return [{ id: '', label: trKey("console.js.panels.routing.inherit_model") }, ...list];
    }
    if (effProv === 'codex') {
      const list = normalizeModelOptions((provs.codex || {}).available_models, ['gpt-5.4', 'gpt-5.3-codex', 'o3', 'o4-mini']);
      return [{ id: '', label: trKey("console.js.panels.routing.inherit_model") }, ...list];
    }
    if (effProv === 'openai') {
      const list = normalizeModelOptions((provs.openai || {}).available_models, ['gpt-4o-mini', 'gpt-4o', 'gpt-5']);
      return [{ id: '', label: trKey("console.js.panels.routing.inherit_model") }, ...list];
    }
    return [{ id: '', label: trKey("console.js.panels.routing.mock") }];
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
      const text = item.id === '' ? (item.label || trKey("console.js.panels.routing.inherit_model")) : item.id;
      html += `<option value="${esc(item.id)}" ${item.id === prev ? 'selected' : ''}>${esc(text)}</option>`;
    }
    if (prev && !hasPrev) {
      html += `<option value="${esc(prev)}" selected>${esc(prev)} (${esc(trKey("console.js.panels.routing.current"))})</option>`;
    }
    mEl.innerHTML = html;
    if (eEl) eEl.disabled = (effProv === 'mock');
  }

  function computeEffectiveSlotsFromDom(cfg) {
    const payload = document.getElementById('prov-active-select') ? collectProviderFormPayload() : null;
    const summarySlots = (cfg.lineage_summary && cfg.lineage_summary.effective_slots) || {};
    if (!payload) return summarySlots;
    const active = payload.active_provider || cfg.active_provider || 'mock';
    const provs = payload.providers || {};
    const cfgGem = (cfg.providers && cfg.providers.gemini) || {};
    const out = {};
    for (const def of FEATURE_SLOT_DEFS) {
      const slot = def.slot;
      const r = (payload.feature_routing && payload.feature_routing[slot]) || { provider: 'inherit', model: '', effort: '' };
      let effProv = (!r.provider || r.provider === 'inherit') ? active : r.provider;
      let baseModel = '';
      let baseEffort = '';
      if (effProv === 'gemini') {
        baseModel = provs.gemini.model || '';
        baseEffort = provs.gemini.effort || '';
      } else if (effProv === 'antigravity') {
        baseModel = (provs.antigravity && provs.antigravity.model) || '';
        baseEffort = (provs.antigravity && provs.antigravity.effort) || '';
      } else if (effProv === 'codex') {
        baseModel = (provs.codex && provs.codex.model) || 'codex-default';
        baseEffort = (provs.codex && provs.codex.effort) || '';
      } else if (effProv === 'openai') {
        baseModel = (provs.openai && provs.openai.model) || '';
      } else if (effProv === 'mock') {
        baseModel = 'mock';
      }
      out[slot] = { provider: effProv, model: r.model || baseModel, effort: r.effort || baseEffort };
    }
    const embedProv = active === 'mock' ? 'mock' : 'gemini';
    const embedModel = active === 'mock' ? 'mock-embed-64d' : (cfgGem.embed_model || 'gemini-embedding-001');
    out.embed = { provider: embedProv, model: embedModel, effort: '' };
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
    const jevLabel = jevCfg && jevCfg.enabled ? `jev:${jevCfg.mode || 'filter'} (${trKey("console.js.panels.lineage.after_gates")})` : "jev:off (" + trKey("console.js.panels.lineage.llm_direct") + ")";
    const jevSub = jevCfg && jevCfg.enabled ? trKey("console.js.panels.lineage.jev_thresholds", { filter: jevCfg.filter_threshold ?? 0.2, merge: jevCfg.auto_merge_threshold ?? 0.9 }) : trKey("console.js.panels.lineage.llm_judge");
    const box = (x, y, w, h, title, sub1, sub2, stroke) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="var(--sec-bg,#f6f8fa)" stroke="${stroke || '#6e7781'}" stroke-width="1.4"/><text x="${x + w / 2}" y="${y + 18}" text-anchor="middle" font-size="11" font-weight="700" fill="currentColor">${esc(title)}</text><text x="${x + w / 2}" y="${y + 34}" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.85">${esc(sub1)}</text><text x="${x + w / 2}" y="${y + 49}" text-anchor="middle" font-size="9.5" fill="currentColor" opacity="0.72">${esc(sub2)}</text>`;
    const arrow = (x1, y1, x2, y2, label) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#6e7781" stroke-width="1.4" marker-end="url(#prov-lineage-arrow)"/>${label ? `<text x="${(x1 + x2) / 2}" y="${Math.min(y1, y2) - 6}" text-anchor="middle" font-size="8.5" fill="currentColor" opacity="0.78">${esc(label)}</text>` : ''}`;
    let svg = '<svg id="prov-lineage-svg" viewBox="0 0 920 196" width="100%" style="max-width:920px;height:auto;display:block;border:1px solid var(--border);border-radius:6px;background:var(--card-bg,#fff);color:var(--fg,#1f2328);">';
    svg += '<defs><marker id="prov-lineage-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#6e7781"/></marker></defs>';
    svg += box(10, 62, 118, 64, trKey("console.js.panels.lineage.box_input"), trKey("console.js.panels.lineage.box_input_sub"), 'PDF/Web/STT/VLM', '#6e7781');
    svg += arrow(128, 82, 152, 46);
    svg += arrow(128, 104, 152, 138);
    svg += box(154, 14, 156, 64, trKey("console.js.panels.lineage.box_extract"), eg.line1, eg.line2, '#0969da');
    svg += box(154, 108, 156, 64, trKey("console.js.panels.lineage.box_summary"), sm.line1, sm.line2, '#8250df');
    svg += arrow(310, 46, 338, 72);
    svg += arrow(310, 140, 338, 106);
    svg += box(340, 54, 166, 68, trKey("console.js.panels.lineage.box_embed"), `${em.line1}:${em.line2}`, trKey("console.js.panels.lineage.box_embed_sub"), '#1a7f37');
    svg += arrow(506, 88, 558, 88, trKey("console.js.panels.lineage.arrow_json"));
    svg += box(560, 54, 168, 68, '4. JEV System 1', jevLabel, jevSub, '#9a6700');
    svg += arrow(728, 76, 752, 46);
    svg += arrow(728, 98, 752, 136);
    svg += box(754, 14, 156, 64, trKey("console.js.panels.lineage.box_judge"), `${ej.line1} / ${rj.line1}`, `${ej.line2}`, '#bf3989');
    svg += box(754, 108, 156, 64, trKey("console.js.panels.lineage.box_graph"), 'doc.meta.model_lineage', trKey("console.js.panels.lineage.box_graph_sub"), '#1f883d');
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
    h += '<strong style="font-size:13px;display:block;margin-bottom:4px;"><span data-icon="git-branch"></span> ' + esc(trKey("console.js.panels.lineage.title")) + '</strong>';
    h += '<p style="margin:0 0 8px;font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.lineage.desc")) + '</p>';
    h += `<div id="prov-lineage-svg-host" style="margin-bottom:10px;">${renderLineageSvg(computeEffectiveSlotsFromDom(cfg), jev)}</div>`;

    const jevStats = lineage.jev || {};
    h += `<div style="font-size:11px;margin-bottom:8px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--card-bg,#fff);">${esc(trKey("console.js.panels.lineage.jev_telemetry"))} ${esc(trKey("console.js.panels.lineage.jev_pairs"))} <b>${Number(jevStats.shadow_pairs || 0)}</b> · ${esc(trKey("console.js.panels.lineage.jev_ok"))} <b>${Number(jevStats.ok_calls || 0)}</b> · ${esc(trKey("console.js.panels.lineage.jev_latency"))} <b>${jevStats.avg_latency_ms != null ? jevStats.avg_latency_ms + 'ms' : '-'}</b> · ${esc(trKey("console.js.panels.lineage.jev_below"))} <b>${Number(jevStats.below_filter_threshold || 0)}</b></div>`;

    const specs = Array.isArray(lineage.architecture_specs) ? lineage.architecture_specs : [];
    if (specs.length > 0) {
      h += '<details open style="margin-bottom:8px;font-size:11px;"><summary style="cursor:pointer;font-weight:600;">' + esc(trKey("console.js.panels.lineage.fixed_title")) + '</summary>';
      h += '<table id="prov-architecture-spec-table" style="width:100%;border-collapse:collapse;margin-top:6px;font-size:11px;">';
      h += '<thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.lineage.col_stage")) + '</th><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.lineage.col_fixed")) + '</th><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.lineage.col_code")) + '</th><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.lineage.col_reason")) + '</th></tr></thead><tbody>';
      for (const sp of specs) {
        h += `<tr style="border-bottom:1px solid var(--border);"><td style="padding:3px 5px;font-weight:600;">${esc(sp.stage)}</td><td style="padding:3px 5px;"><code>${esc(sp.fixed_value)}</code></td><td style="padding:3px 5px;"><code>${esc(sp.scope)}</code></td><td style="padding:3px 5px;">${esc(sp.rationale)}</td></tr>`;
      }
      h += '</tbody></table></details>';
    }

    const fallbacks = Array.isArray(lineage.fallbacks) ? lineage.fallbacks : [];
    if (fallbacks.length > 0) {
      h += '<details style="margin-bottom:8px;font-size:11px;"><summary style="cursor:pointer;font-weight:600;">' + esc(trKey("console.js.panels.lineage.fallback_title")) + '</summary>';
      h += '<table id="prov-fallback-catalog-table" style="width:100%;border-collapse:collapse;margin-top:6px;font-size:11px;">';
      h += '<thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:3px 5px;">ID</th><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.lineage.col_location")) + '</th><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.col_status")) + '</th><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.col_description")) + '</th></tr></thead><tbody>';
      for (const fb of fallbacks) {
        const badge = fb.status === 'REMOVED' ? trKey("console.js.panels.lineage.fallback_removed") : trKey("console.js.panels.lineage.fallback_excluded");
        h += `<tr style="border-bottom:1px solid var(--border);"><td style="padding:3px 5px;"><code>${esc(fb.id)}</code></td><td style="padding:3px 5px;"><code>${esc(fb.location)}</code></td><td style="padding:3px 5px;">${esc(badge)}</td><td style="padding:3px 5px;">${esc(fb.description)}</td></tr>`;
      }
      h += '</tbody></table></details>';
    }

    const recDocs = Array.isArray(lineage.recent_documents) ? lineage.recent_documents : [];
    h += '<details style="font-size:11px;"' + (recDocs.length > 0 ? ' open' : '') + '><summary style="cursor:pointer;font-weight:600;">' + esc(trKey("console.js.panels.lineage.recent_title", { count: recDocs.length })) + '</summary>';
    if (recDocs.length === 0) {
      h += '<p style="margin:6px 0 0;opacity:0.75;">' + esc(trKey("console.js.panels.lineage.recent_empty")) + '</p>';
    } else {
      h += '<table id="prov-recent-lineage-table" style="width:100%;border-collapse:collapse;margin-top:6px;font-size:11px;">';
      h += '<thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.lineage.col_document")) + '</th><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.lineage.col_models")) + '</th><th style="padding:3px 5px;">JEV</th><th style="padding:3px 5px;">' + esc(trKey("console.js.panels.lineage.col_fallback")) + '</th></tr></thead><tbody>';
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
      { id: 'inherit', label: trKey("console.js.panels.routing.inherit_provider") },
      { id: 'gemini', label: 'Google AI Studio (gemini)' },
      { id: 'mock', label: 'Mock' }
    ];

    let h = '<div id="prov-feature-routing-card" style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:4px;"><span data-icon="layers"></span> ' + esc(trKey("console.js.panels.routing.title")) + '</strong>';
    h += '<p style="margin:0 0 8px;font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.routing.desc")) + '</p>';
    h += '<div style="overflow-x:auto;"><table style="width:100%;border-collapse:collapse;font-size:11.5px;">';
    h += '<thead><tr style="border-bottom:1px solid var(--border);text-align:left;"><th style="padding:4px 6px;">' + esc(trKey("console.js.panels.routing.col_slot")) + '</th><th style="padding:4px 6px;">' + esc(trKey("console.js.panels.col_provider")) + '</th><th style="padding:4px 6px;">' + esc(trKey("console.js.panels.col_model")) + '</th><th style="padding:4px 6px;">' + esc(trKey("console.js.panels.col_effort")) + '</th></tr></thead><tbody>';
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
        const mLabel = m.id === '' ? (m.label || trKey("console.js.panels.routing.inherit_model")) : m.id;
        h += `<option value="${esc(m.id)}" ${(r.model || '') === m.id ? 'selected' : ''}>${esc(mLabel)}</option>`;
      }
      if (r.model && !matchedModel) {
        h += `<option value="${esc(r.model)}" selected>${esc(r.model)} (${esc(trKey("console.js.panels.routing.current"))})</option>`;
      }
      h += '</select></td>';
      const effortDis = (effProv === 'mock') ? ' disabled' : '';
      h += `<td style="padding:5px 6px;"><select id="prov-route-${esc(def.slot)}-effort"${effortDis} style="width:100%;box-sizing:border-box;">`;
      for (const eOpt of [['', trKey("console.js.panels.routing.inherit_effort")], ['low', 'low'], ['medium', 'medium'], ['high', 'high']]) {
        h += `<option value="${esc(eOpt[0])}" ${(r.effort || '') === eOpt[0] ? 'selected' : ''}>${esc(eOpt[1])}</option>`;
      }
      h += '</select></td>';
      h += '</tr>';
    }
    h += '</tbody></table></div>';
    h += '<div id="prov-routing-embed-note" style="margin-top:8px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--card-bg,#fff);font-size:11px;opacity:0.9;">' + esc(trKey("console.js.panels.routing.embed_fixed_notice")) + '</div>';
    h += '</div>';
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
      ['expand', trKey("console.js.panels.tier.feature_expand")],
      ['stt', trKey("console.js.panels.tier.feature_stt")],
      ['vlm', trKey("console.js.panels.tier.feature_vlm")],
      ['presentation', trKey("console.js.panels.tier.feature_presentation")],
      ['synthesize', trKey("console.js.panels.tier.feature_synthesize")]
    ];
    let h = '<div id="prov-member-tier-card" style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:4px;"><span data-icon="shield"></span> ' + esc(trKey("console.js.panels.tier.title")) + '</strong>';
    h += '<p style="margin:0 0 8px;font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.tier.desc")) + ' <a href="/admin/server">' + esc(trKey("console.js.panels.tier.desc_link")) + '</a></p>';
    h += '<div style="display:flex;gap:8px;font-size:12px;margin-bottom:8px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.tier.provider")) + '</label>';
    h += '<select id="prov-tier-provider" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="none" ${(tier.provider || 'none') === 'none' ? 'selected' : ''}>${esc(trKey("console.js.panels.tier.provider_none"))}</option>`;
    h += `<option value="gemini" ${tier.provider === 'gemini' ? 'selected' : ''}>Google AI Studio (gemini)</option>`;
    h += '</select></div>';
    h += '<div style="flex:1.2;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.tier.model")) + '</label>';
    h += '<select id="prov-tier-model" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="" ${!tier.model ? 'selected' : ''}>${esc(trKey("console.js.panels.tier.model_none"))}</option>`;
    let matched = false;
    for (const m of geminiModels) {
      if (m.id === tier.model) matched = true;
      h += `<option value="${esc(m.id)}" ${tier.model === m.id ? 'selected' : ''}>${esc(m.id)}</option>`;
    }
    if (tier.model && !matched) {
      h += `<option value="${esc(tier.model)}" selected>${esc(tier.model)}</option>`;
    }
    h += '</select></div>';
    h += '<div style="flex:0.8;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.tier.max_effort")) + '</label>';
    h += '<select id="prov-tier-max-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    for (const eff of ['none', 'minimal', 'low', 'medium', 'high']) {
      h += `<option value="${eff}" ${(tier.max_effort || 'medium') === eff ? 'selected' : ''}>${eff}</option>`;
    }
    h += '</select></div>';
    h += '</div>';
    h += '<div style="font-size:11px;"><span style="opacity:0.8;display:block;margin-bottom:4px;">' + esc(trKey("console.js.panels.tier.features")) + '</span><div style="display:flex;flex-wrap:wrap;gap:10px;">';
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

    let h = '<h2><span data-icon="bolt"></span> ' + esc(trKey("console.js.panels.providers.title_full")) + '</h2>';
    h += '<p class="al">' + esc(trKey("console.js.panels.providers.desc")) + ' <code>data/providers.json</code></p>';
    h += alertHtml;

    h += renderPipelineLineageHtml(cfg);

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<label style="font-weight:600;font-size:13px;display:block;margin-bottom:6px;">' + esc(trKey("console.js.panels.providers.main_provider")) + '</label>';
    h += '<select id="prov-active-select" style="width:100%;padding:6px 8px;font-size:13px;border-radius:6px;border:1px solid var(--border);background:var(--card-bg);color:var(--fg);box-sizing:border-box;">';
    const opts = [
      {val: 'gemini', label: trKey("console.js.panels.providers.gemini_label")},
      {val: 'mock', label: trKey("console.js.panels.providers.mock_label")}
    ];
    for (const opt of opts) {
      h += `<option value="${opt.val}" ${active === opt.val ? 'selected' : ''}>${opt.label}</option>`;
    }
    h += '</select>';
    h += `<div style="margin-top:6px;font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.providers.active_now"))} <b>${esc(cfg.effective_provider || active)}</b> · ${esc(trKey("console.js.panels.providers.active_stt"))} <b>${esc(cfg.effective_stt_provider || 'mock')}</b> · ${esc(trKey("console.js.panels.providers.cli_note"))} <a href="/settings/keys">${esc(trKey("console.js.panels.providers.cli_note_link"))}</a></div>`;
    h += '<div id="prov-feat-mock" style="margin-top:6px;"></div>';
    h += '<div id="prov-cap-mock" style="display:none;margin-top:4px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;font-size:11px;"></div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px;">';
    h += '<strong style="font-size:13px;"><span data-icon="globe"></span> Google AI Studio</strong>';
    h += '<button type="button" class="sec" style="font-size:11px;padding:2px 8px;" onclick="testProviderConnection(\'gemini\')">' + esc(trKey("console.js.panels.providers.check_api")) + '</button>';
    h += '</div>';
    h += '<div id="prov-feat-gemini"></div>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div><label style="font-size:11px;opacity:0.8;">Google AI Studio API Key</label>';
    h += '<div style="display:flex;gap:4px;margin-top:2px;">';
    h += `<input id="prov-gemini-key" type="password" style="flex:1;box-sizing:border-box;" placeholder="${gemini.has_api_key ? '" + esc(trKey("console.js.panels.providers.key_set")) + "' : '" + esc(trKey("console.js.panels.providers.key_placeholder")) + "'}"/>`;
    h += '<button type="button" class="sec" style="font-size:11px;padding:2px 6px;" onclick="togglePassVisibility(\'prov-gemini-key\')" title="'+esc(trKey('console.js.panels.providers.toggle_key'))+'" aria-label="'+esc(trKey('console.js.panels.providers.toggle_key'))+'"><span data-icon="eye"></span></button>';
    if (gemini.has_api_key) {
      h += '<button type="button" class="sec danger-btn" style="font-size:11px;padding:2px 6px;color:var(--err,#cf222e);" onclick="clearApiKeyInput(\'prov-gemini-key\', \'gemini\')">' + esc(trKey("common.action.delete")) + '</button>';
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
    h += '<div><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.providers.model_label")) + '</label>';
    h += renderModelSelectField('prov-gemini-model', gemini.available_models, geminiModelFallback, gemini.model || 'gemini-3.8-flash', 'gemini-3.8-flash');
    h += '</div>';
    h += '<div style="display:flex;gap:8px;align-items:flex-start;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.providers.effort_label")) + '</label>';
    h += '<select id="prov-gemini-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="low" ${gemini.effort==='low'?'selected':''}>low</option>`;
    h += `<option value="medium" ${gemini.effort==='medium'||!gemini.effort?'selected':''}>medium</option>`;
    h += `<option value="high" ${gemini.effort==='high'?'selected':''}>high</option>`;
    h += '</select></div>';
    h += '<div style="flex:1.4;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.providers.embed_model_label")) + '</label>';
    h += `<div id="prov-gemini-embed-info" style="margin-top:2px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--card-bg,#fff);font-size:11.5px;line-height:1.4;"><div><code>${esc(gemini.embed_model || 'gemini-embedding-001')}</code> <span style="opacity:0.75;">(Google AI Studio · 3072d)</span></div><div style="font-size:10.5px;opacity:0.8;margin-top:2px;">${esc(trKey("console.js.panels.providers.embed_fixed_purpose"))}</div></div>`;
    h += '</div>';
    h += '</div>';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.providers.min_interval"))}</label><input id="prov-gemini-interval" type="number" step="0.5" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${gemini.min_interval||4.0}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.providers.max_retries"))}</label><input id="prov-gemini-retries" type="number" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${gemini.max_retries||5}"/></div>`;
    h += '</div>';
    h += '</div>';
    h += '<div id="prov-cap-gemini" style="display:none;margin-top:6px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;font-size:11px;"></div>';
    h += '<div id="prov-test-res-gemini" style="display:none;margin-top:8px;font-size:11px;"></div>';
    h += '</div>';

    h += renderFeatureRoutingHtml(cfg);
    h += renderMemberTierCardHtml(cfg);

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="puzzle"></span> ' + esc(trKey("console.js.panels.providers.aux_title")) + '</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:10px;font-size:12px;">';
    h += '<div>';
    h += `<label style="cursor:pointer;font-weight:600;"><input id="prov-jev-enabled" type="checkbox" ${jev.enabled?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>${esc(trKey("console.js.panels.providers.jev_label"))}</label>`;
    h += '<div style="margin-top:6px;display:flex;gap:4px;">';
    h += `<input id="prov-jev-key" type="password" style="flex:1;box-sizing:border-box;" placeholder="${jev.has_api_key ? '" + esc(trKey("console.js.panels.providers.jev_key_set")) + "' : 'Jev API Key'}"/>`;
    h += '<button type="button" class="sec" style="font-size:11px;padding:2px 6px;" onclick="togglePassVisibility(\'prov-jev-key\')" title="'+esc(trKey('console.js.panels.providers.toggle_key'))+'" aria-label="'+esc(trKey('console.js.panels.providers.toggle_key'))+'"><span data-icon="eye"></span></button>';
    h += '</div>';
    h += `<input id="prov-jev-url" style="width:100%;box-sizing:border-box;margin-top:4px;" placeholder="Base URL" value="${esc(jev.base_url||'https://api.typesafe.ai/v1')}"/>`;
    const curJevMode = jev.mode || 'filter';
    const curFilterThr = jev.filter_threshold != null ? jev.filter_threshold : 0.20;
    const curAutoThr = jev.auto_merge_threshold != null ? jev.auto_merge_threshold : 0.90;
    h += '<div style="display:flex;gap:8px;margin-top:6px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.providers.jev_mode")) + '</label><select id="prov-jev-mode" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="filter" ${curJevMode==='filter'?'selected':''}>filter (${esc(trKey("console.js.panels.providers.jev_mode_filter"))})</option>`;
    h += `<option value="hybrid" ${curJevMode==='hybrid'?'selected':''}>hybrid (${esc(trKey("console.js.panels.providers.jev_mode_hybrid"))})</option>`;
    h += `<option value="shadow" ${curJevMode==='shadow'?'selected':''}>shadow (${esc(trKey("console.js.panels.providers.jev_mode_shadow"))})</option>`;
    h += '</select></div>';
    h += `<div style="flex:0.7;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.providers.jev_filter_threshold"))}</label><input id="prov-jev-filter-threshold" type="number" step="0.05" min="0" max="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(String(curFilterThr))}"/></div>`;
    h += `<div style="flex:0.7;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.providers.jev_merge_threshold"))}</label><input id="prov-jev-auto-merge-threshold" type="number" step="0.05" min="0" max="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(String(curAutoThr))}"/></div>`;
    h += '</div>';
    h += '</div>';
    h += '<div style="border-top:1px dashed var(--border);padding-top:8px;">';
    h += `<label style="cursor:pointer;font-weight:600;"><input id="prov-stt-enabled" type="checkbox" ${stt.enabled!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>${esc(trKey("console.js.panels.providers.stt_label"))}</label>`;
    h += '<div style="display:flex;gap:8px;margin-top:6px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.providers.stt_provider")) + '</label>';
    h += '<select id="prov-stt-prov" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="gemini" ${stt.provider==='gemini'?'selected':''}>Google AI Studio (API)</option>`;
    h += `<option value="mock" ${stt.provider==='mock'?'selected':''}>Mock</option>`;
    h += '</select></div>';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.providers.stt_language"))}</label><input id="prov-stt-lang" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${esc(stt.language||'ko')}"/></div>`;
    h += '</div>';
    h += '<div id="prov-cap-stt" style="display:none;margin-top:6px;padding:6px 8px;border:1px solid rgba(154,103,0,0.35);background:rgba(154,103,0,0.08);color:var(--warn,#9a6700);border-radius:4px;font-size:11px;"></div>';
    h += '</div>';
    h += '</div>';
    h += '</div>';

    const optsState = _provState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    h += '<div id="prov-cap-summary" style="display:none;margin-top:12px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font-size:12px;"></div>';
    h += '<div style="display:flex;gap:8px;margin-top:12px;">';
    h += '<button id="prov-save-btn" type="button" class="btn" style="flex:1;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="saveProviderSettings()"><span data-icon="save"></span> ' + esc(trKey("console.js.panels.save_settings")) + '</button>';
    if (canClose) {
      h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="closeProviderManager()">' + esc(trKey("common.action.close")) + '</button>';
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
    target.innerHTML = '<h2><span data-icon="bolt"></span> ' + esc(trKey("console.js.panels.providers.title")) + '</h2><p class="hint">' + esc(trKey("console.js.panels.loading_settings")) + '</p>';
    try {
      const url = resolveRootUrl(_provState.opts, 'providers');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _provState.config = await r.json();
      renderProviderManagerDOM();
    } catch (e) {
      target.innerHTML = `<h2><span data-icon="bolt"></span> ${esc(trKey("console.js.panels.providers.title"))}</h2><p class="hint" style="color:var(--err,#cf222e)">${esc(trKey("console.js.panels.load_failed"))} ${esc(e.message || e)}</p>`;
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
    const autoExpand = document.getElementById('limit-auto-expand').checked;
    const watchIntervalDays = document.getElementById('limit-watch-interval-days').value;
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
        auto_expand: autoExpand,
        watch_interval_days: watchIntervalDays,
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
        renderLimitManagerDOM(trKey("console.js.panels.save_failed") + ' ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      const resData = await r.json();
      _limitState.config = resData.config;
      renderLimitManagerDOM(trKey("console.js.panels.limits.saved"), true);
      if (_limitState.opts && typeof _limitState.opts.onSave === 'function') {
        _limitState.opts.onSave(resData);
      }
    } catch (e) {
      renderLimitManagerDOM(trKey("console.js.panels.error_occurred") + ' ' + (e.message || e), false);
    }
  }

  async function resetLimitSettings() {
    if (!confirm(trKey("console.js.panels.limits.reset_confirm"))) return;
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
        auto_expand: true,
        watch_interval_days: 1,
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
        renderLimitManagerDOM(trKey("console.js.panels.limits.reset_failed") + ' ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      const resData = await r.json();
      _limitState.config = resData.config;
      renderLimitManagerDOM(trKey("console.js.panels.limits.reset_done"), true);
      if (_limitState.opts && typeof _limitState.opts.onSave === 'function') {
        _limitState.opts.onSave(resData);
      }
    } catch (e) {
      renderLimitManagerDOM(trKey("console.js.panels.error_occurred") + ' ' + (e.message || e), false);
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

    let h = '<h2><span data-icon="ruler"></span> ' + esc(trKey("console.js.panels.limits.title_full")) + '</h2>';
    h += '<p class="al">' + esc(trKey("console.js.panels.limits.desc")) + ' <code>data/ingest_limits.json</code></p>';
    h += alertHtml;

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="globe"></span> ' + esc(trKey("console.js.panels.limits.web_title")) + '</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div>';
    h += '<label style="font-size:11px;opacity:0.8;display:block;">' + esc(trKey("console.js.panels.limits.raw_budget")) + '</label>';
    h += `<input id="limit-raw-char-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.raw_char_budget ?? 20000}"/>`;
    h += '<div style="font-size:10px;opacity:0.7;margin-top:2px;">' + esc(trKey("console.js.panels.limits.raw_budget_hint")) + '</div>';
    h += '</div>';
    h += '</div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="doc"></span> ' + esc(trKey("console.js.panels.limits.pdf_title")) + '</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.pdf_max_extract"))}</label><input id="limit-pdf-max-extract" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.pdf_max_extract_chars ?? 50000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.pdf_paper_threshold"))}</label><input id="limit-pdf-paper-threshold" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.pdf_paper_threshold_chars ?? 15000}"/></div>`;
    h += '</div>';
    h += '<div><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.limits.pdf_parser")) + '</label>';
    h += '<select id="limit-pdf-parser" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += '<option value="default" selected>' + esc(trKey("console.js.panels.limits.pdf_parser_default")) + '</option>';
    h += '</select></div>';
    h += '<div style="display:flex;flex-direction:column;gap:4px;margin-top:4px;">';
    h += `<label style="cursor:pointer;"><input id="limit-pdf-exclude-appendix" type="checkbox" ${lims.pdf_exclude_appendix!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>${esc(trKey("console.js.panels.limits.pdf_exclude_appendix"))}</label>`;
    h += `<label style="cursor:pointer;"><input id="limit-pdf-exclude-references" type="checkbox" ${lims.pdf_exclude_references!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>${esc(trKey("console.js.panels.limits.pdf_exclude_references"))}</label>`;
    h += `<label style="cursor:pointer;"><input id="limit-pdf-detect-encoding-flaws" type="checkbox" ${lims.pdf_detect_encoding_flaws!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>${esc(trKey("console.js.panels.limits.pdf_detect_flaws"))}</label>`;
    h += '</div>';
    h += '<div style="display:flex;gap:8px;margin-top:4px;">';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.limits.pdf_paper_effort")) + '</label>';
    h += '<select id="limit-pdf-paper-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="low" ${lims.pdf_paper_effort==='low'?'selected':''}>low</option>`;
    h += `<option value="medium" ${lims.pdf_paper_effort==='medium'?'selected':''}>medium</option>`;
    h += `<option value="high" ${(lims.pdf_paper_effort||'high')==='high'?'selected':''}>high</option>`;
    h += '</select></div>';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.limits.pdf_default_effort")) + '</label>';
    h += '<select id="limit-pdf-default-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="" ${!lims.pdf_default_effort?'selected':''}>${esc(trKey("console.js.panels.limits.effort_default"))}</option>`;
    h += `<option value="low" ${lims.pdf_default_effort==='low'?'selected':''}>low</option>`;
    h += `<option value="medium" ${lims.pdf_default_effort==='medium'?'selected':''}>medium</option>`;
    h += `<option value="high" ${lims.pdf_default_effort==='high'?'selected':''}>high</option>`;
    h += '</select></div>';
    h += '<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.limits.pdf_classify_effort")) + '</label>';
    h += '<select id="limit-pdf-classifier-effort" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="low" ${(lims.pdf_classifier_effort||'low')==='low'?'selected':''}>low</option>`;
    h += `<option value="medium" ${lims.pdf_classifier_effort==='medium'?'selected':''}>medium</option>`;
    h += `<option value="high" ${lims.pdf_classifier_effort==='high'?'selected':''}>high</option>`;
    h += '</select></div>';
    h += '</div>';
    h += '</div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="puzzle"></span> ' + esc(trKey("console.js.panels.limits.kg_title")) + '</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.extract_budget"))}</label><input id="limit-extract-char-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.extract_char_budget ?? 20000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.merged_budget"))}</label><input id="limit-merged-extract-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.merged_extract_char_budget ?? 0}"/></div>`;
    h += '</div>';
    h += '<div><label style="font-size:11px;opacity:0.8;">' + esc(trKey("console.js.panels.limits.slicing")) + '</label>';
    h += '<select id="limit-slicing-strategy" style="width:100%;box-sizing:border-box;margin-top:2px;">';
    h += `<option value="table-exemption" ${(lims.slicing_strategy||'table-exemption')==='table-exemption'?'selected':''}>${esc(trKey("console.js.panels.limits.slicing_table"))}</option>`;
    h += `<option value="strict" ${lims.slicing_strategy==='strict'?'selected':''}>${esc(trKey("console.js.panels.limits.slicing_strict"))}</option>`;
    h += '</select></div>';
    h += '</div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="compass"></span> ' + esc(trKey("console.js.panels.limits.context_title")) + '</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.embed_budget"))}</label><input id="limit-embed-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.embed_char_budget ?? 8000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.research_budget"))}</label><input id="limit-research-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.research_context_budget ?? 8000}"/></div>`;
    h += '</div>';
    h += '<div>';
    h += `<label style="font-size:11px;opacity:0.8;display:block;">${esc(trKey("console.js.panels.limits.expand_budget"))}</label><input id="limit-expand-budget" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.expand_char_budget ?? 2000}"/>`;
    h += '</div>';
    h += '</div>';
    h += '</div>';

    h += '<div id="limit-expand-section" style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="link"></span> ' + esc(trKey("console.js.panels.limits.expand_title")) + '</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += `<label style="cursor:pointer;"><input id="limit-auto-expand" type="checkbox" ${lims.auto_expand!==false?'checked':''} style="width:auto;margin-right:6px;vertical-align:middle;"/>${esc(trKey("console.js.panels.limits.auto_expand"))}</label>`;
    h += '<div style="font-size:10px;opacity:0.7;">' + esc(trKey("console.js.panels.limits.auto_expand_hint")) + '</div>';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.expand_max"))}</label><input id="limit-expand-max" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.expand_max ?? 5}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.watch_interval_days"))}</label><input id="limit-watch-interval-days" type="number" min="0.01" step="any" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.watch_interval_days ?? 1}"/></div>`;
    h += '</div>';
    h += '<div style="font-size:10px;opacity:0.7;">' + esc(trKey("console.js.panels.limits.watch_interval_hint")) + '</div>';
    h += '</div>';
    h += '</div>';

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--sec-bg);margin-bottom:14px;">';
    h += '<strong style="font-size:13px;display:block;margin-bottom:8px;"><span data-icon="film"></span> ' + esc(trKey("console.js.panels.limits.media_title")) + '</strong>';
    h += '<div style="display:flex;flex-direction:column;gap:8px;font-size:12px;">';
    h += '<div>';
    h += '<label style="font-size:11px;opacity:0.8;display:block;">' + esc(trKey("console.js.panels.limits.presentation_pdf_max")) + '</label>';
    h += `<input id="limit-presentation-pdf-bytes" type="number" min="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.presentation_pdf_max_bytes ?? 67108864}"/>`;
    h += '</div>';
    h += '<div style="display:flex;gap:8px;">';
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.video_max_extract"))}</label><input id="limit-video-max-extract" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.video_max_extract_chars ?? 200000}"/></div>`;
    h += `<div style="flex:1;"><label style="font-size:11px;opacity:0.8;">${esc(trKey("console.js.panels.limits.video_chunk"))}</label><input id="limit-video-chunk-sec" type="number" min="1" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.video_chunk_duration_sec ?? 240}"/></div>`;
    h += '</div>';
    h += '<div>';
    h += '<label style="font-size:11px;opacity:0.8;display:block;">' + esc(trKey("console.js.panels.limits.vlm_max_images")) + '</label>';
    h += `<input id="limit-vlm-max-images" type="number" min="0" style="width:100%;box-sizing:border-box;margin-top:2px;" value="${lims.vlm_max_images ?? 20}"/>`;
    h += '</div>';
    h += '</div>';
    h += '</div>';

    const optsState = _limitState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    h += '<div style="display:flex;gap:8px;margin-top:16px;">';
    h += '<button type="button" class="btn" style="flex:1;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="saveLimitSettings()"><span data-icon="save"></span> ' + esc(trKey("console.js.panels.save_settings")) + '</button>';
    h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="resetLimitSettings()"><span data-icon="restore"></span> ' + esc(trKey("console.js.panels.limits.reset")) + '</button>';
    if (canClose) {
      h += '<button type="button" class="sec" style="padding:8px 12px;" onclick="closeLimitManager()">' + esc(trKey("common.action.close")) + '</button>';
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
    target.innerHTML = '<h2><span data-icon="ruler"></span> ' + esc(trKey("console.js.panels.limits.title")) + '</h2><p class="hint">' + esc(trKey("console.js.panels.loading_settings")) + '</p>';
    try {
      const url = resolveRootUrl(_limitState.opts, 'limits');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _limitState.config = await r.json();
      renderLimitManagerDOM();
    } catch (e) {
      target.innerHTML = `<h2><span data-icon="ruler"></span> ${esc(trKey("console.js.panels.limits.title"))}</h2><p class="hint" style="color:var(--err,#cf222e)">${esc(trKey("console.js.panels.load_failed"))} ${esc(e.message || e)}</p>`;
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

  // The resource of one knowledge base (design Section 10.1): the personal one is the root path of the caller ("/"), a shared one is "kb/<number>/".
  function kbResourceUrl(themeId, query) {
    const tObj = findTheme(themeId);
    const number = (tObj && !tObj.is_personal && tObj.number != null) ? String(tObj.number) : '';
    return resolveRootUrl(_themeState.opts, (number ? 'kb/' + encodeURIComponent(number) + '/' : '') + (query || ''));
  }

  async function updateThemeFromUI(themeId) {
    const labelEl = document.getElementById('editthemep-label-' + themeId);
    const descEl = document.getElementById('editthemep-desc-' + themeId);
    const iconEl = document.getElementById('editthemep-icon-' + themeId);
    const pubEl = document.getElementById('editthemep-pub-' + themeId);
    const presEl = document.getElementById('editthemep-pres-' + themeId);
    const gaEl = document.getElementById('editthemep-ga-' + themeId);
    const sorcererEl = document.getElementById('editthemep-sorcerer-' + themeId);

    const label = ((labelEl || {}).value || '').trim();
    if (!label) { alert(trKey("console.js.panels.themes.name_needed")); return; }
    const description = ((descEl || {}).value || '').trim();
    const icon = ((iconEl || {}).value || '📁').trim() || '📁';

    const tObj = findTheme(themeId);
    const sendId = (tObj && tObj.number != null && String(tObj.number) !== '0') ? String(tObj.number) : String(themeId);  // the server takes only the number (CT23)
    const is_public = pubEl ? pubEl.checked : true;
    const payload = { label, description, icon, is_public };
    if (sorcererEl !== null && sorcererEl !== undefined) {
      payload.sorcerer = sorcererEl.value.trim();
    }
    if (presEl) {
      payload.presentation_enabled = presEl.checked;
    }
    if (gaEl) {
      payload.ga_measurement_id = gaEl.value.trim();
    }

    try {
      const url = kbResourceUrl(themeId);
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(trKey("console.js.panels.themes.update_failed") + ' ' + (err.detail || err.error || ('HTTP ' + r.status)));
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
      alert(trKey("console.js.panels.error_prefix") + ' ' + e);
    }
  }

  async function toggleThemeVisibility(themeId, newVisibility) {
    try {
      const tObj = findTheme(themeId);
      const sendId = (tObj && tObj.number != null && String(tObj.number) !== '0') ? String(tObj.number) : String(themeId);  // the server takes only the number (CT23)
      const url = kbResourceUrl(themeId);
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ is_public: newVisibility }) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(trKey("console.js.panels.themes.visibility_failed") + ' ' + (err.detail || err.error || ('HTTP ' + r.status)));
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
      alert(trKey("console.js.panels.error_prefix") + ' ' + e);
    }
  }

  async function toggleThemePresentation(themeId, newPres) {
    try {
      const tObj = findTheme(themeId);
      const sendId = (tObj && tObj.number != null && String(tObj.number) !== '0') ? String(tObj.number) : String(themeId);  // the server takes only the number (CT23)
      const url = kbResourceUrl(themeId);
      const r = await fetch(url, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ presentation_enabled: Boolean(newPres) }) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(trKey("console.js.panels.themes.presentation_failed") + ' ' + (err.detail || err.error || ('HTTP ' + r.status)));
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
      alert(trKey("console.js.panels.error_prefix") + ' ' + e);
    }
  }

  async function createThemeFromUI() {
    const labelEl = document.getElementById('newthemep-label');
    const descEl = document.getElementById('newthemep-desc');
    const iconEl = document.getElementById('newthemep-icon');
    const presEl = document.getElementById('newthemep-pres');
    const gaEl = document.getElementById('newthemep-ga');
    const sorcererEl = document.getElementById('newthemep-sorcerer');

    const label = ((labelEl || {}).value || '').trim();
    if (!label) { alert(trKey("console.js.panels.themes.name_needed")); return; }
    const description = ((descEl || {}).value || '').trim();
    const icon = ((iconEl || {}).value || '📁').trim() || '📁';
    const presentation_enabled = presEl ? presEl.checked : false;
    const ga_measurement_id = ((gaEl || {}).value || '').trim() || null;
    const sorcerer = ((sorcererEl || {}).value || '').trim();

    // A shared knowledge base is always public and needs a knowledge type (CT5, CT12).
    const knowledge_type = ((document.getElementById('newthemep-type') || {}).value || '').trim();
    const payload = { label, description, icon, knowledge_type, presentation_enabled };
    if (sorcerer) payload.sorcerer = sorcerer;
    if (ga_measurement_id) payload.ga_measurement_id = ga_measurement_id;

    try {
      const url = resolveRootUrl(_themeState.opts, 'kb');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(trKey("console.js.panels.themes.create_failed") + ' ' + (err.detail || err.error || ('HTTP ' + r.status)));
        return;
      }
      const resData = await r.json().catch(() => ({}));
      if (_themeState.opts && typeof _themeState.opts.onThemeChange === 'function') {
        await _themeState.opts.onThemeChange('create', (resData.knowledge_base && resData.knowledge_base.id) || null);
      }
      if (_themeState.opts && typeof _themeState.opts.onThemeChanged === 'function') {
        await _themeState.opts.onThemeChanged('create', (resData.knowledge_base && resData.knowledge_base.id) || null);
      }
      await renderThemeManager(_themeState.container, _themeState.opts);
    } catch (e) {
      alert(trKey("console.js.panels.error_prefix") + ' ' + e);
    }
  }

  async function deleteThemeFromUI(themeId, label) {
    const themeStr = String(themeId);
    if (themeStr === '0') {
      alert(trKey("console.js.panels.themes.default_no_delete"));
      return;
    }
    try {
      // PRINCIPLES D3: the first request gives the plan only. The delete needs a recent sign-in (design Section 6.8).
      const planResponse = await fetch(kbResourceUrl(themeId), { method: 'DELETE' });
      const plan = await planResponse.json().catch(() => ({}));
      if (planResponse.status === 401 && plan.error === 'reauth_required' && _themeState.opts && typeof _themeState.opts.onReauthRequired === 'function') {
        _themeState.opts.onReauthRequired(() => deleteThemeFromUI(themeId, label));
        return;
      }
      if (!planResponse.ok) {
        alert(trKey("console.js.panels.themes.delete_failed") + ' ' + (plan.detail || plan.error || ('HTTP ' + planResponse.status)));
        return;
      }
      const counts = plan.would_delete || {};
      const confirmed = confirm(trKey("console.js.panels.themes.delete_confirm", { label: label }) + '\n' + trKey("console.js.panels.themes.delete_plan", { documents: counts.documents || 0, entities: counts.entities || 0, relations: counts.relations || 0, members: counts.members || 0 }));
      if (!confirmed) return;
      const r = await fetch(kbResourceUrl(themeId, '?apply=true'), { method: 'DELETE' });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        alert(trKey("console.js.panels.themes.delete_failed") + ' ' + (err.detail || err.error || ('HTTP ' + r.status)));
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
      alert(trKey("console.js.panels.error_prefix") + ' ' + e);
    }
  }

  function renderThemeManagerDOM() {
    const target = _themeState.container;
    if (!target) return;
    const themes = _themeState.themes || [];
    const optsState = _themeState.opts || {};
    const isOwner = evalBool(optsState.isOwner, evalBool(optsState.canWrite, true));
    // Only the admin deletes a shared knowledge base (design Section 10.1).
    const isAdmin = evalBool(optsState.isAdmin, false);
    const defaultMgr = optsState.defaultSorcerer || (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.sorcerer) || 'owner';

    let h = '<h2><span data-icon="folder"></span> ' + esc(trKey("console.js.panels.themes.title")) + '</h2>';
    if (isOwner) {
      h += '<p class="al">' + esc(trKey("console.js.panels.themes.desc_owner")) + '</p>';
    } else {
      h += '<p class="al">' + esc(trKey("console.js.panels.themes.desc_reader")) + '</p>';
    }

    h += '<div class="theme-manager-list" style="display:flex;flex-direction:column;gap:10px;margin-bottom:16px;">';
    for (const t of themes) {
      const themeKey = getThemeKey(t);
      const isPub = t.is_public !== false;
      const pubBadge = isPub ? '<span style="color:var(--accent,#00ffaa);font-weight:600;font-size:12px;background:rgba(0,255,170,0.1);padding:2px 6px;border-radius:4px">' + esc(trKey("console.js.panels.themes.public")) + '</span>' : '<span style="color:var(--warn,#f39c12);font-weight:600;font-size:12px;background:rgba(243,156,18,0.1);padding:2px 6px;border-radius:4px">' + esc(trKey("console.js.panels.themes.private")) + '</span>';
      const toggleBtn = isPub ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeVisibility(${jsArg(themeKey)}, false)">${esc(trKey("console.js.panels.themes.make_private"))}</button>` : `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeVisibility(${jsArg(themeKey)}, true)">${esc(trKey("console.js.panels.themes.make_public"))}</button>`;
      const isPres = t.presentation_enabled === true;
      const presBadge = isPres ? '<span style="color:#38bdf8;font-weight:600;font-size:12px;background:rgba(56,189,248,0.12);padding:2px 6px;border-radius:4px" title="' + esc(trKey("console.js.panels.themes.presentation_on_title")) + '"><span data-icon="screen"></span> ' + esc(trKey("console.js.panels.themes.presentation_on")) + '</span>' : '<span style="color:var(--muted,#888);font-weight:600;font-size:12px;background:rgba(136,136,136,0.1);padding:2px 6px;border-radius:4px" title="' + esc(trKey("console.js.panels.themes.presentation_off_title")) + '"><span data-icon="block"></span> ' + esc(trKey("console.js.panels.themes.presentation_off")) + '</span>';
      const togglePresBtn = isPres ? `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemePresentation(${jsArg(themeKey)}, false)">${esc(trKey("console.js.panels.themes.presentation_turn_off"))}</button>` : `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemePresentation(${jsArg(themeKey)}, true)">${esc(trKey("console.js.panels.themes.presentation_turn_on"))}</button>`;
      const idLabel = themeKey === '0' ? trKey("console.js.panels.themes.id_default") : trKey("console.js.panels.themes.id_label", { id: t.number ? t.number : '#' + t.id });
      const rawMgr = (t.sorcerer || t.knowledge_manager || '').trim();
      const effMgr = rawMgr || (t.effective_sorcerer || defaultMgr);
      const legacyHostsBadge = (t.legacy_hosts && t.legacy_hosts.length) ? `<span style="color:#a78bfa;font-weight:600;font-size:12px;background:rgba(167,139,250,0.15);padding:2px 6px;border-radius:4px" title="${esc(trKey("console.js.panels.themes.legacy_title"))}"><span data-icon="globe"></span> ${esc(trKey("console.js.panels.themes.legacy_count", { count: t.legacy_hosts.length }))}</span>` : '';

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
      h += presBadge;
      if (isOwner) h += togglePresBtn;
      if (isOwner) h += `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="showThemeEditForm(${jsArg(themeKey)})"><span data-icon="edit"></span> ${esc(trKey("console.js.panels.themes.edit"))}</button>`;
      h += `<button type="button" class="sec" style="font-size:11px;padding:3px 8px;" onclick="toggleThemeOptionsView(${jsArg(themeKey)})"><span data-icon="settings"></span> ${esc(trKey("console.js.panels.themes.show_options"))}</button>`;
      if (isAdmin && themeKey !== '0') {
        h += `<button type="button" class="sec danger-btn" style="font-size:11px;padding:3px 8px;color:var(--err,#cf222e);border-color:rgba(207,34,46,0.3);" onclick="deleteThemeFromUI(${jsArg(themeKey)}, ${jsArg(t.label)})"><span data-icon="trash"></span> ${esc(trKey("common.action.delete"))}</button>`;
      }
      h += '</div></div>';
      if (t.description) h += `<p style="margin:4px 0 0;font-size:12px;opacity:0.8">${esc(t.description)}</p>`;
      h += `<p style="margin:4px 0 0;font-size:12px;opacity:0.8">${esc(trKey("console.js.panels.themes.manager_prefix"))} ${esc(effMgr)}</p>`;

      h += `<div id="theme-options-view-${themeKey}" style="display:none;margin-top:10px;padding:8px 10px;border-top:1px dashed var(--border);background:var(--panel-bg);border-radius:4px;font-size:12px;flex-direction:column;gap:6px;">`;
      h += '<div style="font-weight:600;color:var(--accent2);margin-bottom:2px;"><span data-icon="settings"></span> ' + esc(trKey("console.js.panels.themes.options_title")) + '</div>';
      h += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_label"))}</b>: ${esc(t.label)}</div>`;
      h += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_manager"))}</b>: ${esc(effMgr)}</div>`;
      h += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_icon"))}</b>: ${esc(t.icon || '📁')}</div>`;
      h += `<div>• <b>${esc(trKey("console.js.panels.col_description"))}</b>: ${esc(t.description || esc(trKey("console.js.panels.themes.no_description")))}</div>`;
      h += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_legacy"))}</b>: ${(t.legacy_hosts && t.legacy_hosts.length) ? t.legacy_hosts.map(lh => esc(trKey("console.js.panels.themes.legacy_host", { host: lh.host, activated: lh.activated_at || '-', sunset: lh.sunset_at || '-' }))).join(', ') : esc(trKey("console.js.panels.themes.none"))}</div>`;
      h += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_ga"))}</b>: ${esc(t.ga_measurement_id || esc(trKey("console.js.panels.themes.ga_unset")))}</div>`;
      h += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_visibility"))}</b>: ${isPub ? esc(trKey("console.js.panels.themes.visibility_public")) : esc(trKey("console.js.panels.themes.private"))}</div>`;
      h += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_presentation"))}</b>: ${isPres ? '<span style="color:#38bdf8;font-weight:600;">' + esc(trKey("console.js.panels.themes.presentation_enabled")) + '</span>' : '<span style="color:var(--muted);font-weight:600;">' + esc(trKey("console.js.panels.themes.presentation_disabled")) + '</span>'}</div>`;
      if (t.stats) h += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_stats"))}</b>: ${esc(trKey("console.js.panels.themes.stats", { documents: t.stats.documents || 0, entities: t.stats.entities || 0, relations: t.stats.relations || 0 }))}</div>`;
      h += '</div>';

      if (isOwner) {
        h += `<div id="theme-edit-form-${themeKey}" style="display:none;margin-top:10px;padding-top:8px;border-top:1px dashed var(--border);flex-direction:column;gap:8px;">`;
        h += `<div><label style="font-size:11px;opacity:0.8">${esc(trKey("console.js.panels.themes.opt_label"))}</label><input id="editthemep-label-${themeKey}" style="width:100%;box-sizing:border-box" value="${esc(t.label)}"/></div>`;
        h += `<div><label style="font-size:11px;opacity:0.8">${esc(trKey("console.js.panels.themes.opt_manager"))}</label><input id="editthemep-sorcerer-${themeKey}" style="width:100%;box-sizing:border-box" placeholder="${esc(trKey("console.js.panels.themes.opt_manager"))}" value="${esc(rawMgr)}"/></div>`;
        h += `<div><label style="font-size:11px;opacity:0.8">${esc(trKey("console.js.panels.col_description"))}</label><input id="editthemep-desc-${themeKey}" style="width:100%;box-sizing:border-box" value="${esc(t.description || '')}"/></div>`;
        if (t.legacy_hosts && t.legacy_hosts.length) {
          h += `<div style="font-size:11px;color:var(--muted);padding:2px 0;"><span data-icon="globe"></span> ${esc(trKey("console.js.panels.themes.legacy_note"))} ${t.legacy_hosts.map(lh => esc(lh.host)).join(', ')}</div>`;
        }
        h += `<div><label style="font-size:11px;opacity:0.8">${esc(trKey("console.js.panels.themes.ga_label"))}</label><input id="editthemep-ga-${themeKey}" style="width:100%;box-sizing:border-box" placeholder="${esc(trKey("console.js.panels.themes.ga_placeholder"))}" value="${esc(t.ga_measurement_id || '')}"/></div>`;
        h += '<div style="display:flex;gap:8px;flex-wrap:wrap;">';
        h += `<div style="flex:1;min-width:80px;"><label style="font-size:11px;opacity:0.8">${esc(trKey("console.js.panels.themes.opt_icon"))}</label><input id="editthemep-icon-${themeKey}" style="width:100%;box-sizing:border-box" value="${esc(t.icon || '📁')}"/></div>`;
        h += `<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="editthemep-pub-${themeKey}" type="checkbox" ${isPub ? 'checked' : ''} style="width:auto;margin-right:4px;vertical-align:middle;"/>${esc(trKey("console.js.panels.themes.public_checkbox"))}</label></div>`;
        h += `<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="editthemep-pres-${themeKey}" type="checkbox" ${isPres ? 'checked' : ''} style="width:auto;margin-right:4px;vertical-align:middle;"/>${esc(trKey("console.js.panels.themes.presentation_checkbox"))}</label></div>`;
        h += '</div>';
        h += '<div style="display:flex;gap:6px;margin-top:4px;align-items:center;">';
        h += `<button type="button" style="padding:4px 12px;" onclick="updateThemeFromUI(${jsArg(themeKey)})">${esc(trKey("common.action.save"))}</button>`;
        h += `<button type="button" class="sec" style="padding:4px 12px;" onclick="hideThemeEditForm(${jsArg(themeKey)})">${esc(trKey("common.action.cancel"))}</button>`;
        if (isAdmin && themeKey !== '0') {
          h += `<button type="button" class="sec danger-btn" style="margin-left:auto;padding:4px 10px;font-size:11px;color:var(--err,#cf222e);border-color:rgba(207,34,46,0.3);" onclick="deleteThemeFromUI(${jsArg(themeKey)}, ${jsArg(t.label)})"><span data-icon="trash"></span> ${esc(trKey("console.js.panels.themes.delete_full"))}</button>`;
        }
        h += '</div></div>';
      }
      h += '</div>';
    }
    h += '</div>';

    if (isOwner) {
      h += '<details style="border:1px solid var(--border);border-radius:6px;padding:8px 10px;background:var(--sec-bg);"><summary style="cursor:pointer;font-weight:600;font-size:13px;"><span data-icon="plus"></span> ' + esc(trKey("console.js.panels.themes.add_title")) + '</summary>';
      h += '<div style="display:flex;flex-direction:column;gap:8px;margin-top:8px;">';
      h += '<div><label style="font-size:11px;opacity:0.8">' + esc(trKey("console.js.panels.themes.opt_label")) + '</label><input id="newthemep-label" style="width:100%;box-sizing:border-box" placeholder="' + esc(trKey("console.js.panels.themes.label_placeholder")) + '"/></div>';
      h += '<div><label for="newthemep-type" style="font-size:11px;opacity:0.8">' + esc(trKey("console.js.panels.themes.type_label")) + '</label><select id="newthemep-type" style="width:100%;box-sizing:border-box"><option value="real">' + esc(trKey("console.js.panels.themes.type_real")) + '</option><option value="fictional">' + esc(trKey("console.js.panels.themes.type_fictional")) + '</option></select></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">' + esc(trKey("console.js.panels.themes.opt_manager")) + '</label><input id="newthemep-sorcerer" style="width:100%;box-sizing:border-box" placeholder="' + esc(trKey("console.js.panels.themes.opt_manager")) + '"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">' + esc(trKey("console.js.panels.col_description")) + '</label><input id="newthemep-desc" style="width:100%;box-sizing:border-box" placeholder="' + esc(trKey("console.js.panels.themes.desc_placeholder")) + '"/></div>';
      h += '<div><label style="font-size:11px;opacity:0.8">' + esc(trKey("console.js.panels.themes.ga_label")) + '</label><input id="newthemep-ga" style="width:100%;box-sizing:border-box" placeholder="' + esc(trKey("console.js.panels.themes.ga_placeholder")) + '"/></div>';
      h += '<div style="display:flex;gap:8px;flex-wrap:wrap;"><div style="flex:1;min-width:80px;"><label style="font-size:11px;opacity:0.8">' + esc(trKey("console.js.panels.themes.opt_icon")) + '</label><input id="newthemep-icon" style="width:100%;box-sizing:border-box" value="📁"/></div>';
      h += '<div style="flex:1;display:flex;align-items:center;padding-top:14px;"><label style="font-size:12px;cursor:pointer;"><input id="newthemep-pres" type="checkbox" style="width:auto;margin-right:4px;vertical-align:middle;"/>' + esc(trKey("console.js.panels.themes.presentation_checkbox")) + '</label></div></div>';
      h += '<button type="button" style="align-self:flex-start;padding:4px 12px;margin-top:4px;" onclick="createThemeFromUI()">' + esc(trKey("console.js.panels.themes.create")) + '</button>';
      h += '</div></details>';
    } else {
      h += '<p class="hint" style="margin-top:14px;font-size:12px;"><span data-icon="info"></span> ' + esc(trKey("console.js.panels.themes.ask_owner")) + '</p>';
    }

    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    if (canClose) {
      h += '<div style="margin-top:16px;">';
      h += '<button type="button" class="sec" style="padding:8px 12px;width:100%;" onclick="closeThemeManager()">' + esc(trKey("common.action.close")) + '</button>';
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
    target.innerHTML = '<h2><span data-icon="folder"></span> ' + esc(trKey("console.js.panels.themes.title")) + '</h2><p class="hint">' + esc(trKey("console.js.panels.themes.loading")) + '</p>';
    try {
      // The personal knowledge base of the caller and the shared knowledge bases: all of them for the admin, else those where the caller is an active member (design Section 7).
      const isAdmin = evalBool(_themeState.opts.isAdmin, false);
      const url = resolveRootUrl(_themeState.opts, isAdmin ? 'kb' : 'kb?joined=true');
      const r = await fetch(url, { headers: { Accept: 'application/json' } });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      _themeState.themes = [data.personal_kb, ...(data.knowledge_bases || [])].filter(Boolean);
      renderThemeManagerDOM();
    } catch (e) {
      target.innerHTML = '<p class="hint">' + esc(trKey("console.js.panels.themes.load_failed")) + ' ' + esc(String(e)) + '</p>';
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
        alert(trKey("console.js.panels.tokens.copied"));
      }).catch(() => {
        el.select();
        document.execCommand('copy');
        alert(trKey("console.js.panels.tokens.copied"));
      });
    } else {
      el.select();
      document.execCommand('copy');
      alert(trKey("console.js.panels.tokens.copied"));
    }
  }

  async function createApiToken() {
    const nameInput = document.getElementById('new-token-name');
    const scopeSelect = document.getElementById('new-token-scope');
    const expirySelect = document.getElementById('new-token-expiry');
    if (!nameInput || !nameInput.value.trim()) {
      alert(trKey("console.js.panels.tokens.name_needed"));
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
        renderTokenManagerDOM(trKey("console.js.panels.tokens.issue_failed") + ' ' + (err.detail || ('HTTP ' + r.status)), false);
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
      renderTokenManagerDOM(trKey("console.js.panels.error_occurred") + ' ' + (e.message || e), false);
    }
  }

  async function revokeApiToken(id, name) {
    if (!confirm(trKey("console.js.panels.tokens.revoke_confirm", { id: id, name: name }))) return;
    try {
      const url = resolveRootUrl(_tokenState.opts, 'tokens/revoke');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ id: id }) });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        renderTokenManagerDOM(trKey("console.js.panels.tokens.revoke_failed") + ' ' + (err.detail || ('HTTP ' + r.status)), false);
        return;
      }
      if (_tokenState.opts && typeof _tokenState.opts.onTokenRevoked === 'function') {
        _tokenState.opts.onTokenRevoked(id);
      }
      if (_tokenState.opts && typeof _tokenState.opts.onTokenChange === 'function') {
        _tokenState.opts.onTokenChange('revoke', id);
      }
      await renderTokenManager(_tokenState.container, _tokenState.opts);
      renderTokenManagerDOM(trKey("console.js.panels.tokens.revoked_done", { id: id, name: name }), true);
    } catch (e) {
      renderTokenManagerDOM(trKey("console.js.panels.error_occurred") + ' ' + (e.message || e), false);
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

    let h = '<h2><span data-icon="key"></span> ' + esc(trKey("console.js.panels.tokens.title")) + '</h2>';
    h += '<p class="al">' + esc(trKey("console.js.panels.tokens.desc")) + '</p>';
    const scopeNotices = _tokenState.scopeNotices || [];
    if (scopeNotices.length) {
      h += '<div role="status" style="padding:8px 10px;border-radius:6px;background:rgba(243,156,18,0.1);color:var(--warn,#9a6700);font-size:12px;margin-bottom:12px;border:1px solid var(--warn,#9a6700);"><span data-icon="warning"></span> ' + esc(trKey("console.js.panels.tokens.scope_notice")) + '<ul style="margin:4px 0 0;padding-left:18px;">' + scopeNotices.map(n => '<li>' + esc(n.name) + ' (#' + esc(String(n.id)) + ')</li>').join('') + '</ul></div>';
    }
    h += alertHtml;

    if (activeNewToken) {
      const rawVal = activeNewToken.token || '';
      const mcpOrigin = window.location.origin;
      const mcpUrl = `${mcpOrigin}/mcp`;
      const mcpSnippet = JSON.stringify({ mcpServers: { 'claire-bible': { command: 'npx', args: ['-y', '@modelcontextprotocol/server-everything'], env: { CLAIRE_URL: mcpUrl, CLAIRE_TOKEN: rawVal } } } }, null, 2);
      h += '<div style="background:rgba(26,127,55,0.08);border:2px solid var(--accent2,#1a7f37);border-radius:8px;padding:12px;margin-bottom:16px;">';
      h += '<h3 style="margin:0 0 6px 0;font-size:14px;color:var(--accent2,#1a7f37);"><span data-icon="check-circle"></span> ' + esc(trKey("console.js.panels.tokens.minted_title")) + '</h3>';
      h += '<p style="margin:0 0 8px 0;font-size:12px;">' + esc(trKey("console.js.panels.tokens.minted_hint")) + '</p>';
      h += '<div style="display:flex;gap:6px;margin-bottom:8px;">';
      h += `<input type="text" id="newly-minted-token" readonly value="${esc(rawVal)}" style="flex:1;font-family:monospace;padding:6px 8px;font-size:12px;border:1px solid var(--border);border-radius:4px;background:var(--bg);" onclick="this.select();"/>`;
      h += '<button type="button" class="btn" style="padding:6px 10px;font-size:12px;background:var(--btn-bg,#1f883d);color:#fff;border:none;border-radius:4px;cursor:pointer;" onclick="copyMintedToken()"><span data-icon="clipboard"></span> ' + esc(trKey("console.js.panels.tokens.copy")) + '</button>';
      h += '</div>';
      h += '<details style="font-size:12px;color:var(--fg);"><summary style="cursor:pointer;font-weight:600;margin-bottom:4px;"><span data-icon="clipboard"></span> ' + esc(trKey("console.js.panels.tokens.mcp_json")) + '</summary>';
      h += `<pre style="background:var(--bg);padding:8px;border-radius:4px;overflow-x:auto;font-size:11px;border:1px solid var(--border);">${esc(mcpSnippet)}</pre></details>`;
      h += '</div>';
    }

    h += '<div style="border:1px solid var(--border);border-radius:6px;padding:12px;background:var(--sec-bg);margin-bottom:16px;">';
    h += '<h3 style="margin:0 0 10px 0;font-size:13px;"><span data-icon="plus"></span> ' + esc(trKey("console.js.panels.tokens.new_title")) + '</h3>';
    h += '<div style="display:flex;flex-direction:column;gap:10px;">';
    h += '<div>';
    h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">' + esc(trKey("console.js.panels.tokens.name_label")) + '</label>';
    h += '<input type="text" id="new-token-name" placeholder="' + esc(trKey("console.js.panels.tokens.name_placeholder")) + '" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:13px;"/>';
    h += '</div>';

    h += '<div style="display:flex;gap:12px;flex-wrap:wrap;">';
    h += '<div style="flex:1;min-width:130px;">';
    h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">' + esc(trKey("console.js.panels.tokens.scope_label")) + '</label>';
    h += '<select id="new-token-scope" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;">';
    h += '<option value="readonly" selected>' + esc(trKey("console.js.panels.tokens.scope_readonly")) + '</option>';
    h += '<option value="write">' + esc(trKey("console.js.panels.tokens.scope_write")) + '</option>';
    h += '<option value="owner">' + esc(trKey("console.js.panels.tokens.scope_owner")) + '</option>';
    h += '</select>';
    h += '</div>';

    h += '<div style="flex:1;min-width:130px;">';
    h += '<label style="font-size:12px;font-weight:600;display:block;margin-bottom:4px;">' + esc(trKey("console.js.panels.tokens.expiry_label")) + '</label>';
    h += '<select id="new-token-expiry" style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--fg);font-size:12px;">';
    h += '<option value="0" selected>' + esc(trKey("console.js.panels.tokens.expiry_none")) + '</option>';
    h += '<option value="30">' + esc(trKey("console.js.panels.tokens.expiry_30")) + '</option>';
    h += '<option value="90">' + esc(trKey("console.js.panels.tokens.expiry_90")) + '</option>';
    h += '<option value="365">' + esc(trKey("console.js.panels.tokens.expiry_365")) + '</option>';
    h += '</select>';
    h += '</div>';
    h += '</div>';

    h += '<button type="button" class="btn" style="margin-top:4px;background:var(--btn-bg,#1f883d);color:#fff;font-weight:600;padding:8px 12px;border:none;border-radius:6px;cursor:pointer;" onclick="createApiToken()"><span data-icon="key"></span> ' + esc(trKey("console.js.panels.tokens.issue")) + '</button>';
    h += '</div>';
    h += '</div>';

    h += '<h3 style="margin:0 0 8px 0;font-size:13px;"><span data-icon="clipboard"></span> ' + esc(trKey("console.js.panels.tokens.list_title", { count: tokens.length })) + '</h3>';
    if (!tokens.length) {
      h += '<p class="hint">' + esc(trKey("console.js.panels.tokens.empty")) + '</p>';
    } else {
      h += '<div style="display:flex;flex-direction:column;gap:8px;">';
      for (const tok of tokens) {
        const isRevoked = tok.revoked_at !== null && tok.revoked_at !== undefined;
        const nowTs = Date.now() / 1000;
        const isExpired = tok.expires_at && tok.expires_at < nowTs;
        let statusBadge = '<span style="color:#1a7f37;background:rgba(26,127,55,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">' + esc(trKey("console.js.panels.tokens.status_active")) + '</span>';
        if (isRevoked) {
          statusBadge = '<span style="color:#cf222e;background:rgba(207,34,46,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">' + esc(trKey("console.js.panels.tokens.status_revoked")) + '</span>';
        } else if (isExpired) {
          statusBadge = '<span style="color:#9a6700;background:rgba(154,103,0,0.1);padding:2px 6px;border-radius:4px;font-size:11px;font-weight:600;">' + esc(trKey("console.js.panels.tokens.status_expired")) + '</span>';
        }

        let expStr = trKey("console.js.panels.tokens.expiry_none_short");
        if (tok.expires_at) {
          const d = new Date(tok.expires_at * 1000);
          expStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        }

        let lastUsedStr = trKey("console.js.panels.tokens.never_used");
        if (tok.last_used_at) {
          const lu = new Date(tok.last_used_at * 1000);
          lastUsedStr = lu.toLocaleDateString() + ' ' + lu.toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
        }

        const scopeColors = { owner: 'var(--accent,#0969da)', write: '#8250df', readonly: '#57606a' };
        const scopeCol = scopeColors[tok.scope] || '#57606a';

        h += `<div style="border:1px solid var(--border);border-radius:6px;padding:10px;background:var(--bg);opacity:${isRevoked ? '0.6' : '1'};">`;
        h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px;">';
        h += `<div><strong style="font-size:13px;">#${tok.id} ${esc(tok.name)}</strong> <span style="font-size:11px;font-weight:600;color:${scopeCol};border:1px solid ${scopeCol};padding:1px 5px;border-radius:4px;margin-left:4px;">${esc(tok.scope)}</span> ${statusBadge}</div>`;
        if (!isRevoked) {
          h += `<button type="button" class="sec" style="font-size:11px;padding:2px 8px;color:var(--err,#cf222e);border-color:var(--err,#cf222e);" onclick="revokeApiToken(${tok.id}, ${jsArg(tok.name)})">${esc(trKey("console.js.panels.tokens.revoke"))}</button>`;
        }
        h += '</div>';

        h += '<div style="font-size:12px;color:var(--sec-fg,#57606a);display:flex;flex-direction:column;gap:2px;">';
        h += `<div>${esc(trKey("console.js.panels.tokens.identifier"))} <code style="font-size:11px;background:var(--sec-bg);padding:1px 4px;border-radius:3px;">${esc(tok.token_masked || 'cb_...')}</code></div>`;
        h += `<div>${esc(trKey("console.js.panels.tokens.expires"))}: ${esc(expStr)} · ${esc(trKey("console.js.panels.tokens.last_used"))}: ${esc(lastUsedStr)}</div>`;
        h += '</div>';
        h += '</div>';
      }
      h += '</div>';
    }

    const optsState = _tokenState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    if (canClose) {
      h += '<div style="margin-top:16px;">';
      h += '<button type="button" class="sec" style="padding:8px 12px;width:100%;" onclick="closeTokenManager()">' + esc(trKey("common.action.close")) + '</button>';
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
    target.innerHTML = '<h2><span data-icon="key"></span> ' + esc(trKey("console.js.panels.tokens.title")) + '</h2><p class="hint">' + esc(trKey("console.js.panels.tokens.loading")) + '</p>';
    try {
      const url = resolveRootUrl(_tokenState.opts, 'tokens');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      _tokenState.tokens = data.tokens || [];
      // The server sends each scope notice once (design Section 6.6 rule 2). Keep it for the next renders of this page.
      if (Array.isArray(data.scope_notices) && data.scope_notices.length) _tokenState.scopeNotices = data.scope_notices;
      const initialMsg = (_tokenState.opts && _tokenState.opts.initialMsg) || undefined;
      const initialSuccess = (_tokenState.opts && _tokenState.opts.initialSuccess) || false;
      renderTokenManagerDOM(initialMsg, initialSuccess, _tokenState.newCreatedToken);
    } catch (e) {
      target.innerHTML = `<h2><span data-icon="key"></span> ${esc(trKey("console.js.panels.tokens.title"))}</h2><p class="hint" style="color:var(--err,#cf222e)">${esc(trKey("console.js.panels.tokens.load_failed"))} ${esc(e.message || e)}</p>`;
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
