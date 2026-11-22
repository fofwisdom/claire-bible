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

  function trFallback(key, fallback, params) {
    const val = trKey(key, params);
    return (val && val !== key) ? val : fallback;
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
  const _tokenState = { container: null, opts: {}, tokens: [], newCreatedToken: null, retired: false, deprecationNotice: '' };

  function findTheme(themeRef) {
    const themes = _themeState.themes || (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.themes) || [];
    const ref = String(themeRef ?? '').trim();
    if (!ref || ref === '0') {
      return themes.find(t => t.is_personal || String(t.id) === '0' || t.number === '0') || themes[0] || null;
    }
    return themes.find(t => String(t.number) === ref) || themes.find(t => String(t.id) === ref) || null;
  }

  // Use ClaireConsoleUI when loaded and fall back to matching canonical markup.
  const _ATTR_NAME_RE = /^[a-zA-Z_:][a-zA-Z0-9_.:-]*$/;
  const _SAFE_CARD_TAGS = { section: true, div: true, form: true, article: true };

  function consoleUi() {
    return window.ClaireConsoleUI || null;
  }

  function joinCls() {
    const out = [];
    for (let i = 0; i < arguments.length; i += 1) {
      const part = arguments[i];
      if (part && String(part).trim()) out.push(String(part).trim());
    }
    return out.join(' ');
  }

  function attrsHtml(attrs) {
    if (!attrs || typeof attrs !== 'object') return '';
    const parts = [];
    Object.keys(attrs).forEach(key => {
      if (!_ATTR_NAME_RE.test(key)) return;
      const val = attrs[key];
      if (val === false || val == null) return;
      if (val === true) parts.push(' ' + key);
      else parts.push(' ' + key + '="' + esc(val) + '"');
    });
    return parts.join('');
  }

  function uiIcon(name) {
    const cu = consoleUi();
    if (cu && typeof cu.icon === 'function') return cu.icon(name);
    if (!name) return '';
    if (window.ClaireIcons && typeof window.ClaireIcons.svg === 'function') {
      const svg = window.ClaireIcons.svg(name);
      if (svg) return svg;
    }
    return '<span data-icon="' + esc(name) + '"></span>';
  }

  function uiCard(opts) {
    const cu = consoleUi();
    if (cu && typeof cu.card === 'function') return cu.card(opts);
    const o = opts || {};
    const tag = _SAFE_CARD_TAGS[o.tag] ? o.tag : 'section';
    const cls = joinCls('console-subcard', o.split === false ? 'no-split' : '', o.extraClass);
    const idAttr = o.id ? ' id="' + esc(o.id) + '"' : '';
    const ariaAttr = o.ariaLabel ? ' aria-label="' + esc(o.ariaLabel) + '"' : '';
    const titleContent = o.titleHtml != null ? String(o.titleHtml) : esc(o.title || '');
    const iconPart = o.icon ? uiIcon(o.icon) + ' ' : '';
    const badgePart = o.badgeHtml ? ' ' + String(o.badgeHtml) : '';
    const headingHtml = (titleContent || o.icon || o.badgeHtml) ? '<h2>' + iconPart + titleContent + badgePart + '</h2>' : '';
    const headerHtml = o.actionsHtml ? '<div class="console-card-head">' + headingHtml + String(o.actionsHtml) + '</div>' : headingHtml;
    let hintHtml = '';
    if (o.hintHtml != null && o.hintHtml !== '') hintHtml = '<p class="hint">' + String(o.hintHtml) + '</p>';
    else if (o.hint != null && o.hint !== '') hintHtml = '<p class="hint">' + esc(o.hint) + '</p>';
    const bodyHtml = o.bodyHtml != null ? String(o.bodyHtml) : '';
    return '<' + tag + ' class="' + esc(cls) + '"' + idAttr + ariaAttr + attrsHtml(o.attrs) + '>' + headerHtml + hintHtml + bodyHtml + '</' + tag + '>';
  }

  function uiField(opts) {
    const cu = consoleUi();
    if (cu && typeof cu.field === 'function') return cu.field(opts);
    const o = opts || {};
    const cls = joinCls('console-field', o.extraClass);
    const forAttr = o.id ? ' for="' + esc(o.id) + '"' : '';
    const labelContent = o.labelHtml != null ? String(o.labelHtml) : esc(o.label || '');
    const controlHtml = o.controlHtml != null ? String(o.controlHtml) : '';
    let hintHtml = '';
    if (o.hintHtml != null && o.hintHtml !== '') hintHtml = '<small class="console-field-hint">' + String(o.hintHtml) + '</small>';
    else if (o.hint != null && o.hint !== '') hintHtml = '<small class="console-field-hint">' + esc(o.hint) + '</small>';
    return '<label class="' + esc(cls) + '"' + forAttr + attrsHtml(o.attrs) + '>' + labelContent + controlHtml + hintHtml + '</label>';
  }

  function uiToggleRow(opts) {
    const cu = consoleUi();
    if (cu && typeof cu.toggleRow === 'function') return cu.toggleRow(opts);
    const o = opts || {};
    const idAttr = o.id ? ' id="' + esc(o.id) + '"' : '';
    const labelContent = o.labelHtml != null ? String(o.labelHtml) : esc(o.label || '');
    const labelEl = o.id ? '<label for="' + esc(o.id) + '"><strong>' + labelContent + '</strong></label>' : '<strong>' + labelContent + '</strong>';
    let descHtml = '';
    if (o.descHtml != null && o.descHtml !== '') descHtml = '<span>' + String(o.descHtml) + '</span>';
    else if (o.desc != null && o.desc !== '') descHtml = '<span>' + esc(o.desc) + '</span>';
    const checkedAttr = o.checked ? ' checked' : '';
    const disabledAttr = o.disabled ? ' disabled' : '';
    return '<div class="pref-row"><div class="pref-row-label">' + labelEl + descHtml + '</div><input' + idAttr + ' type="checkbox" switch' + checkedAttr + disabledAttr + attrsHtml(o.attrs) + '/></div>';
  }

  function uiNotice(html, tone, extraClass, attrs) {
    const cu = consoleUi();
    if (cu && typeof cu.notice === 'function') return cu.notice(html, tone, extraClass, attrs);
    const cls = joinCls('pref-notice', extraClass);
    return '<div class="' + esc(cls) + '" data-tone="' + esc(tone || 'info') + '"' + attrsHtml(attrs) + '>' + (html != null ? String(html) : '') + '</div>';
  }

  function uiPill(text, tone, extraClass, attrs) {
    const cu = consoleUi();
    if (cu && typeof cu.pill === 'function') return cu.pill(text, tone, extraClass, attrs);
    const cls = joinCls('pref-pill', extraClass);
    return '<span class="' + esc(cls) + '" data-tone="' + esc(tone || 'neutral') + '"' + attrsHtml(attrs) + '>' + esc(text) + '</span>';
  }

  function uiTable(opts) {
    const o = opts || {};
    const cu = consoleUi();
    if (!o.tableId && cu && typeof cu.table === 'function') return cu.table(o);
    const headers = Array.isArray(o.headers) ? o.headers : [];
    const tblCls = joinCls('pref-table', o.extraClass);
    const idAttr = o.tableId ? ' id="' + esc(o.tableId) + '"' : '';
    const ariaAttr = o.ariaLabel ? ' aria-label="' + esc(o.ariaLabel) + '"' : '';
    const caption = o.captionHtml ? '<caption>' + String(o.captionHtml) + '</caption>' : '';
    let thead = '';
    if (headers.length) {
      thead = '<thead><tr>' + headers.map(h => {
        if (h && typeof h === 'object') {
          const thCls = h.className ? ' class="' + esc(h.className) + '"' : '';
          const thContent = h.html != null ? String(h.html) : esc(h.label || '');
          return '<th' + thCls + attrsHtml(h.attrs) + '>' + thContent + '</th>';
        }
        return '<th>' + esc(h) + '</th>';
      }).join('') + '</tr></thead>';
    }
    const tbodyIdAttr = o.tbodyId ? ' id="' + esc(o.tbodyId) + '"' : '';
    const bodyHtml = o.bodyHtml != null ? String(o.bodyHtml) : '';
    return '<div class="pref-table-wrap"><table' + idAttr + ' class="' + esc(tblCls) + '"' + ariaAttr + '>' + caption + thead + '<tbody' + tbodyIdAttr + '>' + bodyHtml + '</tbody></table></div>';
  }

  function uiBtn(label, opts) {
    const cu = consoleUi();
    if (cu && typeof cu.btn === 'function') return cu.btn(label, opts);
    const o = opts || {};
    const variant = o.variant ? String(o.variant) : '';
    const tokens = variant ? variant.split(/\s+/) : [];
    const isAccent = tokens.indexOf('accent') >= 0 || tokens.indexOf('pri') >= 0 || tokens.indexOf('primary') >= 0;
    const isDanger = tokens.indexOf('danger') >= 0;
    const isSm = Boolean(o.sm) || o.size === 'sm' || tokens.indexOf('sm') >= 0;
    const cls = joinCls('pref-btn', isAccent ? 'pref-btn-accent' : '', isDanger ? 'pref-btn-danger' : '', isSm ? 'pref-btn-sm' : '', o.extraClass);
    const idAttr = o.id ? ' id="' + esc(o.id) + '"' : '';
    const iconPart = o.icon ? uiIcon(o.icon) + ' ' : '';
    const labelContent = o.labelHtml != null ? String(o.labelHtml) : esc(label || '');
    if (o.href != null) {
      return '<a class="' + esc(cls) + '" href="' + esc(o.href) + '"' + idAttr + attrsHtml(o.attrs) + '>' + iconPart + labelContent + '</a>';
    }
    const btnType = o.type ? String(o.type) : 'button';
    const disabledAttr = o.disabled ? ' disabled' : '';
    return '<button type="' + esc(btnType) + '" class="' + esc(cls) + '"' + idAttr + disabledAttr + attrsHtml(o.attrs) + '>' + iconPart + labelContent + '</button>';
  }

  function uiActions(html, extraClass) {
    const cu = consoleUi();
    if (cu && typeof cu.actions === 'function') return cu.actions(html, extraClass);
    return '<div class="' + esc(joinCls('console-actions', extraClass)) + '">' + (html != null ? String(html) : '') + '</div>';
  }

  function renderAlertBanner(msg, isSuccess) {
    if (!msg) return '';
    return uiNotice(`${uiIcon(isSuccess ? 'check-circle' : 'warning')} ${esc(msg)}`, isSuccess ? 'ok' : 'danger', '', { role: 'status', 'aria-live': 'polite' });
  }

  function renderTogglePassBtn(inputId) {
    const label = trKey('console.js.panels.providers.toggle_key');
    return uiBtn('', { sm: true, labelHtml: '<span data-icon="eye"></span>', attrs: { onclick: `togglePassVisibility(${jsArg(inputId)})`, title: label, 'aria-label': label } });
  }

  function renderPanelLoadingCard(cardId, iconName, titleKey, loadingKey) {
    return uiCard({ id: cardId, icon: iconName, title: trKey(titleKey), hint: trKey(loadingKey), split: false, bodyHtml: '' });
  }

  function renderPanelErrorCard(cardId, iconName, titleKey, failKey, err) {
    return uiCard({ id: cardId, icon: iconName, title: trKey(titleKey), split: false, bodyHtml: uiNotice(`${esc(trKey(failKey))} ${esc((err && err.message) || err)}`, 'danger') });
  }

  function enhanceConsoleDom() {
    if (window.ClaireConsoleShell && typeof window.ClaireConsoleShell.enhance === 'function') {
      window.ClaireConsoleShell.enhance();
    } else if (window.ClaireIcons && typeof window.ClaireIcons.hydrate === 'function') {
      window.ClaireIcons.hydrate();
    }
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

  const GEMINI_FALLBACK_MODEL_EFFORTS = [
    ['gemini-3.5-flash-lite', ['minimal', 'low', 'medium', 'high']],
    ['gemini-3.8-flash', ['low', 'medium', 'high']],
    ['gemini-4-argon', ['low', 'medium', 'high']],
    ['gemini-3.6-flash', ['minimal', 'low', 'medium', 'high']],
    ['gemini-3.1-flash-lite', ['minimal', 'low', 'medium', 'high']],
    ['gemini-3.1-pro-preview', ['low', 'medium', 'high']],
    ['gemini-3-flash-preview', ['minimal', 'low', 'medium', 'high']],
    ['gemini-2.5-flash', ['low', 'medium', 'high']],
    ['gemini-2.5-flash-lite', ['low', 'medium', 'high']],
    ['gemini-2.5-pro', ['low', 'medium', 'high']]
  ];

  function fallbackGeminiVariants() {
    const out = [];
    for (const [base, efforts] of GEMINI_FALLBACK_MODEL_EFFORTS) {
      for (const eff of efforts) out.push(`${base}-${eff}`);
    }
    return out;
  }

  function splitGeminiModelEffort(modelName) {
    const raw = String(modelName || '').trim();
    const m = raw.match(/^(.*)-(none|minimal|low|medium|high)$/i);
    if (m && m[1]) return { base: m[1], effort: m[2].toLowerCase() };
    return { base: raw, effort: '' };
  }

  function resolveGeminiVariantSelection(rawList, fallbackList, currentVal, fallbackEffort, defaultId) {
    const items = normalizeModelOptions(rawList, fallbackList);
    const ids = new Set(items.map(m => m.id));
    const cur = String(currentVal || '').trim();
    if (!cur) return defaultId && ids.has(defaultId) ? defaultId : '';
    if (ids.has(cur)) return cur;
    const parsed = splitGeminiModelEffort(cur);
    if (!parsed.effort) {
      const candidate = `${cur}-${String(fallbackEffort || 'medium').toLowerCase()}`;
      if (ids.has(candidate)) return candidate;
    }
    return cur;
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
    let html = `<select id="${esc(id)}" class="pref-select"${disabledAttr}>${res.optionsHtml}</select>`;
    if (res.unsupportedCurrent) {
      html += `<small class="console-field-hint" style="color:var(--danger);">${esc(trKey("console.js.panels.providers.model_unsupported", { model: res.previousVal }))}</small>`;
    }
    return html;
  }

  function setNoticeBox(el, tone, content, isHtml) {
    if (!el) return;
    if (!content) {
      el.hidden = true;
      el.style.display = 'none';
      el.removeAttribute('data-tone');
      el.textContent = '';
      return;
    }
    el.hidden = false;
    el.style.display = '';
    el.dataset.tone = tone || 'info';
    if (isHtml) el.innerHTML = content;
    else el.textContent = content;
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
    const gemEmbedEl = document.getElementById('prov-gemini-embed-model');
    const gemEmbedModel = gemEmbedEl ? gemEmbedEl.value.trim() : '';
    const cfgGemEffort = ((((_provState.config || {}).providers || {}).gemini || {}).effort || 'medium');
    const gemEffort = splitGeminiModelEffort(gemModel).effort || cfgGemEffort;
    const gemInterval = parseFloat(document.getElementById('prov-gemini-interval').value) || 4.0;
    const gemRetries = parseInt(document.getElementById('prov-gemini-retries').value, 10) || 5;

    const jevEnabled = document.getElementById('prov-jev-enabled').checked;
    const jevKey = document.getElementById('prov-jev-key');
    const jevUrl = document.getElementById('prov-jev-url').value.trim();

    const sttEnabled = document.getElementById('prov-stt-enabled').checked;
    const sttProv = document.getElementById('prov-stt-prov').value;
    const sttLang = document.getElementById('prov-stt-lang').value.trim();

    const featureRouting = {};
    const slotNames = ['extract_graph', 'summarize', 'render_detail', 'entity_judge', 'relation_judge', 'classify', 'research', 'stt', 'vlm'];
    for (const slot of slotNames) {
      const pEl = document.getElementById(`prov-route-${slot}-provider`);
      const mEl = document.getElementById(`prov-route-${slot}-model`);
      if (pEl || mEl) {
        const slotProv = pEl ? String(pEl.value || 'inherit') : 'inherit';
        const effProv = resolveEffectiveRouteProvider({ active_provider: activeProv }, slotProv);
        const slotModel = mEl ? String(mEl.value || '').trim() : '';
        featureRouting[slot] = {
          provider: slotProv,
          model: slotModel,
          effort: effProv === 'gemini' ? splitGeminiModelEffort(slotModel).effort : ''
        };
      }
    }

    const payload = {
      active_provider: activeProv,
      feature_routing: featureRouting,
      providers: {
        gemini: { model: gemModel, effort: gemEffort, ...(gemEmbedModel ? { embed_model: gemEmbedModel } : {}), min_interval: gemInterval, max_retries: gemRetries },
        jev: { enabled: jevEnabled, base_url: jevUrl },
        stt: { enabled: sttEnabled, provider: sttProv, language: sttLang }
      }
    };

    const tierProvEl = document.getElementById('prov-tier-provider');
    if (tierProvEl) {
      const tierModelEl = document.getElementById('prov-tier-model');
      const tierModelVal = tierProvEl.value === 'none' ? '' : ((tierModelEl && tierModelEl.value.trim()) || '');
      const prevTierEffort = ((_provState.config && _provState.config.server_tier && _provState.config.server_tier.max_effort) || 'medium');
      const inferredTierEffort = splitGeminiModelEffort(tierModelVal).effort || prevTierEffort;
      const tierFeats = Array.from(document.querySelectorAll('input[name="prov-tier-feature"]:checked')).map((el) => el.value);
      payload.server_tier = {
        provider: tierProvEl.value || 'none',
        model: tierModelVal,
        max_effort: tierProvEl.value === 'none' ? 'low' : inferredTierEffort,
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
      const prefix = ok ? trKey("console.js.panels.providers.supported") : trKey("console.js.panels.providers.unsupported");
      return uiPill(`${prefix} · ${label}`, ok ? 'ok' : 'neutral', '', note ? { title: note } : null);
    };
    const items = [
      chip(trKey("console.js.panels.providers.cap_extract"), Boolean(f.extract)),
      chip(trKey("console.js.panels.providers.cap_research"), Boolean(f.research)),
      chip(trKey("console.js.panels.providers.cap_embedding"), Boolean(f.embedding), cap.embedding_note || ''),
      chip('STT·VLM', Boolean(f.stt_vlm))
    ];
    return uiActions(items.join(''));
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
          setNoticeBox(lockEl, 'danger', trKey("console.js.panels.providers.locked_prefix") + ' ' + cap.locked_reason, false);
        } else if (cap.embedding_note) {
          setNoticeBox(lockEl, 'warn', trKey("console.js.panels.providers.advisory") + ' ' + cap.embedding_note, false);
        } else if (cap.advisory) {
          setNoticeBox(lockEl, 'warn', cap.advisory, false);
        } else {
          setNoticeBox(lockEl, '', '', false);
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
        const resG = buildModelSelectResult(gemCap.available_models, null, gemSel.value, 'gemini-3.8-flash-medium');
        gemSel.innerHTML = resG.optionsHtml;
        gemSel.value = resG.selected;
      }
      const tierModelSel = document.getElementById('prov-tier-model');
      if (tierModelSel && tierModelSel.tagName === 'SELECT') {
        const resT = buildModelSelectResult(gemCap.available_models, null, tierModelSel.value, 'gemini-3.5-flash-lite-medium');
        tierModelSel.innerHTML = '<option value="">' + esc(trKey("console.js.panels.providers.tier_none_option")) + '</option>' + resT.optionsHtml;
        if (tierModelSel.querySelector(`option[value="${CSS.escape(resT.selected)}"]`)) {
          tierModelSel.value = resT.selected;
        }
      }
    }

    const sttEl = document.getElementById('prov-cap-stt');
    if (sttEl && caps.stt) {
      if (caps.stt.enabled && caps.stt.provider === 'gemini' && !caps.stt.ready) {
        setNoticeBox(sttEl, 'warn', trKey("console.js.panels.providers.stt_warning"), false);
      } else {
        setNoticeBox(sttEl, '', '', false);
      }
    }

    const sumEl = document.getElementById('prov-cap-summary');
    if (sumEl) {
      const locks = Array.isArray(caps.lock_reasons) ? caps.lock_reasons : [];
      const warns = Array.isArray(caps.warnings) ? caps.warnings : [];
      if (locks.length > 0) {
        setNoticeBox(sumEl, 'danger', '<strong>' + esc(trKey("console.js.panels.providers.save_locked")) + '</strong><ul>' + locks.map(r => '<li>' + esc(r) + '</li>').join('') + '</ul>', true);
      } else if (warns.length > 0) {
        setNoticeBox(sumEl, 'warn', '<strong>' + esc(trKey("console.js.panels.providers.advisory")) + '</strong><ul>' + warns.map(w => '<li>' + esc(w) + '</li>').join('') + '</ul>', true);
      } else {
        setNoticeBox(sumEl, 'ok', trKey("console.js.panels.providers.check_passed"), false);
      }
    }

    const saveBtn = document.getElementById('prov-save-btn');
    if (saveBtn) {
      const canSave = Boolean(caps.can_save);
      saveBtn.disabled = !canSave;
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
    setNoticeBox(sumEl, 'info', trKey("console.js.panels.providers.check_in_work"), false);
    const payload = collectProviderFormPayload();
    if (opts.live) payload.live = true;
    try {
      const url = resolveRootUrl(_provState.opts, 'providers/check');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal: _capAbortCtrl.signal });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const caps = await r.json();
      applyProviderCapabilities(caps);
    } catch (e) {
      if (e && e.name === 'AbortError') return;
      setNoticeBox(sumEl, 'danger', trKey("console.js.panels.providers.check_failed") + ' ' + (e.message || e), false);
      if (saveBtn) saveBtn.disabled = true;
    }
  }

  function bindProviderCapabilityEvents() {
    const standardIds = [
      'prov-active-select',
      'prov-gemini-key',
      'prov-gemini-model',
      'prov-gemini-embed-model',
      'prov-jev-enabled',
      'prov-stt-enabled',
      'prov-stt-prov',
      'prov-stt-lang'
    ];
    const embedSelEl = document.getElementById('prov-gemini-embed-model');
    const embedLifecycleEl = document.getElementById('prov-gemini-embed-lifecycle');
    const embedInfoEl = document.getElementById('prov-gemini-embed-info');
    if (embedSelEl && embedLifecycleEl) {
      const syncEmbedLifecycleBanner = () => {
        const m = String(embedSelEl.value || 'gemini-embedding-2').trim();
        const isV1 = m === 'gemini-embedding-001';
        const spaceId = isV1 ? 'google:gemini-embedding-001:3072:v1_param' : `google:${m}:3072:v2_prompt`;
        let msg = `<code class="pref-mono">${esc(spaceId)}</code> · 3072d`;
        if (isV1) {
          msg += '<p class="hint" style="margin-top:0.25rem;color:var(--warn);"><b>EOS Warning (2028-05-14):</b> <code class="pref-mono">gemini-embedding-001</code> is scheduled for retirement. Migrate non-blocking with <code class="pref-mono">./cb-manuscript app re-embed --target-space google:gemini-embedding-2:3072:v2_prompt --mode batch</code></p>';
        }
        embedLifecycleEl.innerHTML = msg;
        if (embedInfoEl) embedInfoEl.dataset.tone = isV1 ? 'warn' : 'info';
      };
      embedSelEl.addEventListener('change', syncEmbedLifecycleBanner);
      syncEmbedLifecycleBanner();
    }
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
    if (tierProvEl && tierModelEl) {
      const syncTierState = () => {
        const isNone = tierProvEl.value === 'none';
        tierModelEl.disabled = isNone;
      };
      tierProvEl.addEventListener('change', syncTierState);
      syncTierState();
    }
    const slotNames = ['extract_graph', 'summarize', 'render_detail', 'entity_judge', 'relation_judge', 'classify', 'research', 'stt', 'vlm'];
    for (const slot of slotNames) {
      const pEl = document.getElementById(`prov-route-${slot}-provider`);
      const mEl = document.getElementById(`prov-route-${slot}-model`);
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
    }
  }

  async function testProviderConnection(provName) {
    const resEl = document.getElementById('prov-test-res-' + provName);
    if (resEl) {
      resEl.hidden = false;
      resEl.style.display = '';
      resEl.removeAttribute('data-state');
      resEl.textContent = trKey("console.js.panels.providers.test_in_work");
    }
    const payload = { provider: provName, config: {} };
    if (provName === 'gemini') {
      const kEl = document.getElementById('prov-gemini-key');
      if (kEl && kEl.value) payload.config.api_key = kEl.value;
      const mEl = document.getElementById('prov-gemini-model');
      if (mEl && mEl.value) payload.config.model = mEl.value;
    }
    try {
      const url = resolveRootUrl(_provState.opts, 'providers/test');
      const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const d = await r.json();
      if (resEl) {
        if (d.ok) {
          resEl.dataset.state = 'ok';
          resEl.textContent = '' + (d.message || trKey("console.js.panels.providers.test_ok"));
          if (provName === 'gemini' && d.details && Array.isArray(d.details.models) && d.details.models.length) {
            if (_provState.config && _provState.config.providers && _provState.config.providers.gemini) {
              _provState.config.providers.gemini.available_models = d.details.models;
              if (Array.isArray(d.details.embed_models)) _provState.config.providers.gemini.available_embed_models = d.details.embed_models;
            }
            const gSel = document.getElementById('prov-gemini-model');
            if (gSel) {
              const r1 = buildModelSelectResult(d.details.models, null, gSel.value, 'gemini-3.8-flash-medium');
              gSel.innerHTML = r1.optionsHtml;
              gSel.value = r1.selected;
            }
            for (const sl of ['extract_graph', 'summarize', 'render_detail', 'entity_judge', 'relation_judge', 'classify', 'research', 'stt', 'vlm']) {
              updateRouteSlotModelOptions(sl);
            }
          }
        } else {
          resEl.dataset.state = 'error';
          resEl.textContent = '' + (d.error || trKey("console.js.panels.providers.test_failed"));
        }
      }
      await runProviderCapabilityCheck({ live: provName === 'gemini' });
    } catch (e) {
      if (resEl) {
        resEl.dataset.state = 'error';
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
    { slot: 'render_detail', label: trKey("console.js.panels.slot.render_detail"), note: trKey("console.js.panels.slot.render_detail_note") },
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
      const list = normalizeModelOptions((provs.gemini || {}).available_models, fallbackGeminiVariants());
      return [{ id: '', label: trKey("console.js.panels.routing.inherit_model") }, ...list];
    }
    return [{ id: '', label: trKey("console.js.panels.routing.mock") }];
  }

  function updateRouteSlotModelOptions(slot) {
    const cfg = _provState.config || {};
    const pEl = document.getElementById(`prov-route-${slot}-provider`);
    const mEl = document.getElementById(`prov-route-${slot}-model`);
    if (!pEl || !mEl) return;
    const effProv = resolveEffectiveRouteProvider(cfg, pEl.value);
    const items = modelsForRouteProvider(cfg, effProv, slot);
    const prev = effProv === 'gemini' ? resolveGeminiVariantSelection(items, null, mEl.value, 'medium', '') : mEl.value;
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
        baseEffort = splitGeminiModelEffort(baseModel).effort || provs.gemini.effort || '';
      } else if (effProv === 'mock') {
        baseModel = 'mock';
      }
      const effModel = r.model || baseModel;
      const effEffort = effProv === 'gemini' ? (splitGeminiModelEffort(effModel).effort || r.effort || baseEffort) : (r.effort || baseEffort);
      out[slot] = { provider: effProv, model: effModel, effort: effEffort };
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
    const dt = slotText('render_detail');
    const em = slotText('embed');
    const ej = slotText('entity_judge');
    const rj = slotText('relation_judge');
    const smSub1 = dt.line1 && dt.line1 !== sm.line1 ? `${sm.line1} / ${dt.line1}` : sm.line1;
    const smSub2 = dt.line2 && dt.line2 !== sm.line2 ? `${sm.line2} · ${dt.line2}` : sm.line2;
    const jevLabel = jevCfg && jevCfg.enabled ? "jev:shadow" : "jev:off";
    const jevSub = jevCfg && jevCfg.enabled ? trKey("console.js.panels.lineage.jev_shadow") : trKey("console.js.panels.lineage.llm_judge");
    const box = (x, y, w, h, title, sub1, sub2, stroke) => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="7" fill="var(--sec-bg,#f6f8fa)" stroke="${stroke || '#6e7781'}" stroke-width="1.4"/><text x="${x + w / 2}" y="${y + 18}" text-anchor="middle" font-size="11" font-weight="700" fill="currentColor">${esc(title)}</text><text x="${x + w / 2}" y="${y + 34}" text-anchor="middle" font-size="10" fill="currentColor" opacity="0.85">${esc(sub1)}</text><text x="${x + w / 2}" y="${y + 49}" text-anchor="middle" font-size="9.5" fill="currentColor" opacity="0.72">${esc(sub2)}</text>`;
    const arrow = (x1, y1, x2, y2, label) => `<line x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" stroke="#6e7781" stroke-width="1.4" marker-end="url(#prov-lineage-arrow)"/>${label ? `<text x="${(x1 + x2) / 2}" y="${Math.min(y1, y2) - 6}" text-anchor="middle" font-size="8.5" fill="currentColor" opacity="0.78">${esc(label)}</text>` : ''}`;
    let svg = '<svg id="prov-lineage-svg" viewBox="0 0 920 196" width="100%" style="max-width:920px;height:auto;display:block;border:1px solid var(--border);border-radius:0.5rem;background:var(--card-bg,var(--bg));color:var(--fg);">';
    svg += '<defs><marker id="prov-lineage-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 1 L 10 5 L 0 9 z" fill="#6e7781"/></marker></defs>';
    svg += box(10, 62, 118, 64, trKey("console.js.panels.lineage.box_input"), trKey("console.js.panels.lineage.box_input_sub"), 'PDF/Web/STT/VLM', '#6e7781');
    svg += arrow(128, 82, 152, 46);
    svg += arrow(128, 104, 152, 138);
    svg += box(154, 14, 156, 64, trKey("console.js.panels.lineage.box_extract"), eg.line1, eg.line2, '#0969da');
    svg += box(154, 108, 156, 64, trKey("console.js.panels.lineage.box_summary"), smSub1, smSub2, '#8250df');
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
    const jevSnap = {
      enabled: jevEnabledEl ? jevEnabledEl.checked : Boolean(((cfg.providers || {}).jev || {}).enabled)
    };
    host.innerHTML = renderLineageSvg(computeEffectiveSlotsFromDom(cfg), jevSnap);
  }

  function renderPipelineLineageHtml(cfg) {
    const provs = cfg.providers || {};
    const jev = provs.jev || {};
    const lineage = cfg.lineage_summary || {};
    const jevStats = lineage.jev || {};
    const jevTelemetryHtml = `${esc(trKey("console.js.panels.lineage.jev_telemetry"))} ${esc(trKey("console.js.panels.lineage.jev_pairs"))} <b>${Number(jevStats.shadow_pairs || 0)}</b> · ${esc(trKey("console.js.panels.lineage.jev_ok"))} <b>${Number(jevStats.ok_calls || 0)}</b> · ${esc(trKey("console.js.panels.lineage.jev_latency"))} <b>${jevStats.avg_latency_ms != null ? jevStats.avg_latency_ms + 'ms' : '-'}</b>`;

    let body = `<div id="prov-lineage-svg-host">${renderLineageSvg(computeEffectiveSlotsFromDom(cfg), jev)}</div>`;
    body += uiNotice(jevTelemetryHtml, 'info');

    const specs = Array.isArray(lineage.architecture_specs) ? lineage.architecture_specs : [];
    if (specs.length > 0) {
      let specRows = '';
      for (const sp of specs) {
        specRows += `<tr><td><strong>${esc(sp.stage)}</strong></td><td><code class="pref-mono">${esc(sp.fixed_value)}</code></td><td><code class="pref-mono">${esc(sp.scope)}</code></td><td>${esc(sp.rationale)}</td></tr>`;
      }
      body += `<details class="pref-disclosure" open><summary>${esc(trKey("console.js.panels.lineage.fixed_title"))}</summary>${uiTable({ tableId: 'prov-architecture-spec-table', headers: [trKey("console.js.panels.lineage.col_stage"), trKey("console.js.panels.lineage.col_fixed"), trKey("console.js.panels.lineage.col_code"), trKey("console.js.panels.lineage.col_reason")], bodyHtml: specRows })}</details>`;
    }

    const fallbacks = Array.isArray(lineage.fallbacks) ? lineage.fallbacks : [];
    if (fallbacks.length > 0) {
      let fbRows = '';
      for (const fb of fallbacks) {
        const badge = fb.status === 'REMOVED' ? trKey("console.js.panels.lineage.fallback_removed") : trKey("console.js.panels.lineage.fallback_excluded");
        const pillHtml = uiPill(badge, fb.status === 'REMOVED' ? 'danger' : 'neutral');
        fbRows += `<tr><td><code class="pref-mono">${esc(fb.id)}</code></td><td><code class="pref-mono">${esc(fb.location)}</code></td><td>${pillHtml}</td><td>${esc(fb.description)}</td></tr>`;
      }
      body += `<details class="pref-disclosure"><summary>${esc(trKey("console.js.panels.lineage.fallback_title"))}</summary>${uiTable({ tableId: 'prov-fallback-catalog-table', headers: ['ID', trKey("console.js.panels.lineage.col_location"), trKey("console.js.panels.col_status"), trKey("console.js.panels.col_description")], bodyHtml: fbRows })}</details>`;
    }

    const recDocs = Array.isArray(lineage.recent_documents) ? lineage.recent_documents : [];
    let recentContent = '';
    if (recDocs.length === 0) {
      recentContent = `<p class="hint">${esc(trKey("console.js.panels.lineage.recent_empty"))}</p>`;
    } else {
      let recRows = '';
      for (const d of recDocs.slice(0, 8)) {
        const lin = d.lineage || {};
        const finals = lin.final_output_models || {};
        const modelTags = Object.entries(finals).map(([st, info]) => `${st}=${(info && info.provider) || '?'}:${(info && info.model) || '?'}`).join(', ');
        const jevD = lin.jev || {};
        const jevSummary = jevD.enabled ? `shadow (${Number(jevD.evaluated || 0)})` : 'off';
        const fbCount = Array.isArray(lin.fallbacks_triggered) ? lin.fallbacks_triggered.length : 0;
        recRows += `<tr><td>${esc(d.title || d.id)}</td><td><code class="pref-mono">${esc(modelTags || '-')}</code></td><td>${esc(jevSummary)}</td><td>${fbCount}</td></tr>`;
      }
      recentContent = uiTable({ tableId: 'prov-recent-lineage-table', headers: [trKey("console.js.panels.lineage.col_document"), trKey("console.js.panels.lineage.col_models"), 'JEV', trKey("console.js.panels.lineage.col_fallback")], bodyHtml: recRows });
    }
    body += `<details class="pref-disclosure"${recDocs.length > 0 ? ' open' : ''}><summary>${esc(trKey("console.js.panels.lineage.recent_title", { count: recDocs.length }))}</summary>${recentContent}</details>`;

    return uiCard({
      id: 'prov-lineage-visualizer-card',
      icon: 'fork',
      title: trKey("console.js.panels.lineage.title"),
      hint: trKey("console.js.panels.lineage.desc"),
      split: false,
      bodyHtml: body
    });
  }

  function renderFeatureRoutingHtml(cfg) {
    const routing = cfg.feature_routing || {};
    const routeProvChoices = [
      { id: 'inherit', label: trKey("console.js.panels.routing.inherit_provider") },
      { id: 'gemini', label: 'Google AI Studio (gemini)' },
      { id: 'mock', label: 'Mock' }
    ];

    let rowsHtml = '';
    for (const def of FEATURE_SLOT_DEFS) {
      const r = routing[def.slot] || { provider: 'inherit', model: '', effort: '' };
      const rProv = r.provider || 'inherit';
      const effProv = resolveEffectiveRouteProvider(cfg, rProv);
      const modelOpts = modelsForRouteProvider(cfg, effProv, def.slot);
      const selectedRouteModel = effProv === 'gemini' ? resolveGeminiVariantSelection(modelOpts, null, r.model || '', r.effort || 'medium', '') : (r.model || '');

      let provOptsHtml = '';
      for (const opt of routeProvChoices) {
        provOptsHtml += `<option value="${esc(opt.id)}" ${rProv === opt.id ? 'selected' : ''}>${esc(opt.label)}</option>`;
      }
      let modelOptsHtml = '';
      let matchedModel = false;
      for (const m of modelOpts) {
        if (m.id === selectedRouteModel) matchedModel = true;
        const mLabel = m.id === '' ? (m.label || trKey("console.js.panels.routing.inherit_model")) : m.id;
        modelOptsHtml += `<option value="${esc(m.id)}" ${selectedRouteModel === m.id ? 'selected' : ''}>${esc(mLabel)}</option>`;
      }
      if (selectedRouteModel && !matchedModel) {
        modelOptsHtml += `<option value="${esc(selectedRouteModel)}" selected>${esc(selectedRouteModel)} (${esc(trKey("console.js.panels.routing.current"))})</option>`;
      }

      rowsHtml += `<tr data-route-slot="${esc(def.slot)}">`;
      rowsHtml += `<td><div><strong>${esc(def.label)}</strong></div><div class="hint"><code class="pref-mono">${esc(def.slot)}</code> · ${esc(def.note)}</div></td>`;
      rowsHtml += `<td><select id="prov-route-${esc(def.slot)}-provider" class="pref-select">${provOptsHtml}</select></td>`;
      rowsHtml += `<td><select id="prov-route-${esc(def.slot)}-model" class="pref-select">${modelOptsHtml}</select></td>`;
      rowsHtml += '</tr>';
    }

    const body = uiTable({
      headers: [trKey("console.js.panels.routing.col_slot"), trKey("console.js.panels.col_provider"), trKey("console.js.panels.col_model")],
      bodyHtml: rowsHtml
    }) + uiNotice(esc(trKey("console.js.panels.routing.embed_fixed_notice")), 'info', '', { id: 'prov-routing-embed-note' });

    return uiCard({
      id: 'prov-feature-routing-card',
      icon: 'settings',
      title: trKey("console.js.panels.routing.title"),
      hint: trKey("console.js.panels.routing.desc"),
      split: false,
      bodyHtml: body
    });
  }

  function renderMemberTierCardHtml(cfg) {
    const tier = cfg.server_tier || { provider: 'none', model: '', max_effort: 'medium', features: [] };
    const gemini = (cfg.providers || {}).gemini || {};
    const geminiModels = normalizeModelOptions(gemini.available_models, fallbackGeminiVariants());
    const selectedTierModel = resolveGeminiVariantSelection(geminiModels, null, tier.model || '', tier.max_effort || 'medium', '');
    const tierFeats = Array.isArray(tier.features) ? tier.features : [];
    const featLabels = [
      ['expand', trKey("console.js.panels.tier.feature_expand")],
      ['stt', trKey("console.js.panels.tier.feature_stt")],
      ['vlm', trKey("console.js.panels.tier.feature_vlm")],
      ['presentation', trKey("console.js.panels.tier.feature_presentation")],
      ['synthesize', trKey("console.js.panels.tier.feature_synthesize")]
    ];

    const tierProvHtml = `<select id="prov-tier-provider" class="pref-select"><option value="none" ${(tier.provider || 'none') === 'none' ? 'selected' : ''}>${esc(trKey("console.js.panels.tier.provider_none"))}</option><option value="gemini" ${tier.provider === 'gemini' ? 'selected' : ''}>Google AI Studio (gemini)</option></select>`;
    let tierModelOptsHtml = `<option value="" ${!selectedTierModel ? 'selected' : ''}>${esc(trKey("console.js.panels.tier.model_none"))}</option>`;
    let matched = false;
    for (const m of geminiModels) {
      if (m.id === selectedTierModel) matched = true;
      tierModelOptsHtml += `<option value="${esc(m.id)}" ${selectedTierModel === m.id ? 'selected' : ''}>${esc(m.id)}</option>`;
    }
    if (selectedTierModel && !matched) {
      tierModelOptsHtml += `<option value="${esc(selectedTierModel)}" selected>${esc(selectedTierModel)}</option>`;
    }
    const tierModelHtml = `<select id="prov-tier-model" class="pref-select">${tierModelOptsHtml}</select>`;

    let featCheckHtml = '';
    for (const [fKey, fLabel] of featLabels) {
      const checked = tierFeats.includes(fKey) ? ' checked' : '';
      featCheckHtml += `<label style="display:inline-flex;align-items:center;gap:0.375rem;font-weight:500;cursor:pointer;"><input type="checkbox" name="prov-tier-feature" value="${esc(fKey)}"${checked}/><span>${esc(fLabel)}</span></label>`;
    }

    const body = '<div class="console-form-grid">'
      + uiField({ id: 'prov-tier-provider', label: trKey("console.js.panels.tier.provider"), controlHtml: tierProvHtml })
      + uiField({ id: 'prov-tier-model', label: trKey("console.js.panels.tier.model"), controlHtml: tierModelHtml })
      + '</div>'
      + `<div class="console-field"><span>${esc(trKey("console.js.panels.tier.features"))}</span>${uiActions(featCheckHtml)}</div>`;

    return uiCard({
      id: 'prov-member-tier-card',
      icon: 'shield',
      title: trKey("console.js.panels.tier.title"),
      hintHtml: `${esc(trKey("console.js.panels.tier.desc"))} <a href="/admin/server">${esc(trKey("console.js.panels.tier.desc_link"))}</a>`,
      bodyHtml: body
    });
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

    let h = renderAlertBanner(msg, isSuccess);
    h += renderPipelineLineageHtml(cfg);

    const activeOpts = [
      { val: 'gemini', label: trKey("console.js.panels.providers.gemini_label") },
      { val: 'mock', label: trKey("console.js.panels.providers.mock_label") }
    ];
    let activeSelectHtml = '<select id="prov-active-select" class="pref-select">';
    for (const opt of activeOpts) {
      activeSelectHtml += `<option value="${opt.val}" ${active === opt.val ? 'selected' : ''}>${esc(opt.label)}</option>`;
    }
    activeSelectHtml += '</select>';
    const activeHintHtml = `${esc(trKey("console.js.panels.providers.active_now"))} <b>${esc(cfg.effective_provider || active)}</b> · ${esc(trKey("console.js.panels.providers.active_stt"))} <b>${esc(cfg.effective_stt_provider || 'mock')}</b>`;
    const mainBody = uiField({ id: 'prov-active-select', label: trKey("console.js.panels.providers.main_provider"), controlHtml: activeSelectHtml, hintHtml: activeHintHtml })
      + '<div id="prov-feat-mock"></div>'
      + '<div id="prov-cap-mock" class="pref-notice" hidden></div>';
    h += uiCard({
      id: 'prov-main-provider-card',
      icon: 'bolt',
      title: trKey("console.js.panels.providers.title_full"),
      hintHtml: `${esc(trKey("console.js.panels.providers.desc"))} <code class="pref-mono">data/providers.json</code>`,
      bodyHtml: mainBody
    });

    const gemKeyPlaceholder = gemini.has_api_key ? trKey("console.js.panels.providers.key_set") : trKey("console.js.panels.providers.key_placeholder");
    let gemKeyRow = `<div class="console-form-row"><input id="prov-gemini-key" type="password" class="pref-input" style="flex:1;min-width:12rem;" placeholder="${esc(gemKeyPlaceholder)}"/>${renderTogglePassBtn('prov-gemini-key')}`;
    if (gemini.has_api_key) {
      gemKeyRow += uiBtn(trKey("common.action.delete"), { variant: 'danger', sm: true, attrs: { onclick: "clearApiKeyInput('prov-gemini-key', 'gemini')" } });
    }
    gemKeyRow += '</div>';

    const geminiModelFallback = fallbackGeminiVariants();
    const selectedGeminiModel = resolveGeminiVariantSelection(gemini.available_models, geminiModelFallback, gemini.model || 'gemini-3.8-flash-medium', gemini.effort || 'medium', 'gemini-3.8-flash-medium');
    const selEmbedModel = String(gemini.embed_model || 'gemini-embedding-2').trim() || 'gemini-embedding-2';
    const rawEmbedModels = Array.isArray(gemini.available_embed_models) && gemini.available_embed_models.length ? gemini.available_embed_models.map((x) => (typeof x === 'string' ? x : String((x && x.id) || ''))).filter(Boolean) : ['gemini-embedding-2', 'gemini-embedding-001'];
    const embedChoices = Array.from(new Set(['gemini-embedding-2', 'gemini-embedding-001', ...rawEmbedModels, selEmbedModel].filter((x) => x && x !== 'gemini-embedding-2-preview')));
    let embedOptsHtml = '';
    for (const em of embedChoices) {
      const emTag = em === 'gemini-embedding-2' ? ' (3072d · GA Recommended)' : (em === 'gemini-embedding-001' ? ' (3072d · Legacy, EOS 2028-05-14)' : ' (3072d)');
      embedOptsHtml += `<option value="${esc(em)}" ${selEmbedModel === em ? 'selected' : ''}>${esc(em + emTag)}</option>`;
    }
    const embedInfoHtml = `<div id="prov-gemini-embed-info" class="pref-notice" data-tone="${selEmbedModel === 'gemini-embedding-001' ? 'warn' : 'info'}"><div id="prov-gemini-embed-lifecycle"><code class="pref-mono">google:${esc(selEmbedModel)}:3072:${selEmbedModel === 'gemini-embedding-2' ? 'v2_prompt' : 'v1_param'}</code> · 3072d</div><p class="hint" style="margin-top:0.25rem;">${esc(trKey("console.js.panels.providers.embed_fixed_purpose"))}</p></div>`;

    const gemBody = '<div id="prov-feat-gemini"></div>'
      + uiField({ id: 'prov-gemini-key', label: 'Google AI Studio API Key', controlHtml: gemKeyRow })
      + uiField({ id: 'prov-gemini-model', label: trKey("console.js.panels.providers.model_label"), controlHtml: renderModelSelectField('prov-gemini-model', gemini.available_models, geminiModelFallback, selectedGeminiModel, 'gemini-3.8-flash-medium') })
      + uiField({ id: 'prov-gemini-embed-model', label: trKey("console.js.panels.providers.embed_model_label"), controlHtml: `<select id="prov-gemini-embed-model" class="pref-select">${embedOptsHtml}</select>` })
      + embedInfoHtml
      + '<div class="console-form-grid">'
      + uiField({ id: 'prov-gemini-interval', label: trKey("console.js.panels.providers.min_interval"), controlHtml: `<input id="prov-gemini-interval" type="number" step="0.5" class="pref-input" value="${esc(gemini.min_interval || 4.0)}"/>` })
      + uiField({ id: 'prov-gemini-retries', label: trKey("console.js.panels.providers.max_retries"), controlHtml: `<input id="prov-gemini-retries" type="number" class="pref-input" value="${esc(gemini.max_retries || 5)}"/>` })
      + '</div>'
      + '<div id="prov-cap-gemini" class="pref-notice" hidden></div>'
      + '<p id="prov-test-res-gemini" class="result" role="status" aria-live="polite" hidden></p>';

    h += uiCard({
      id: 'prov-gemini-card',
      icon: 'globe',
      title: 'Google AI Studio',
      actionsHtml: uiActions(uiBtn(trKey("console.js.panels.providers.check_api"), { sm: true, attrs: { onclick: "testProviderConnection('gemini')" } })),
      split: false,
      bodyHtml: gemBody
    });

    h += renderFeatureRoutingHtml(cfg);
    h += renderMemberTierCardHtml(cfg);

    const jevKeyPlaceholder = jev.has_api_key ? trKey("console.js.panels.providers.jev_key_set") : 'Jev API Key';
    const jevKeyRow = `<div class="console-form-row"><input id="prov-jev-key" type="password" class="pref-input" style="flex:1;min-width:12rem;" placeholder="${esc(jevKeyPlaceholder)}"/>${renderTogglePassBtn('prov-jev-key')}</div>`;
    const sttProvSelect = `<select id="prov-stt-prov" class="pref-select"><option value="gemini" ${stt.provider === 'gemini' ? 'selected' : ''}>Google AI Studio (API)</option><option value="mock" ${stt.provider === 'mock' ? 'selected' : ''}>Mock</option></select>`;
    const auxBody = uiToggleRow({ id: 'prov-jev-enabled', label: trKey("console.js.panels.providers.jev_label"), descHtml: `<span id="prov-jev-role">${esc(trKey("console.js.panels.providers.jev_role_shadow"))}</span>`, checked: Boolean(jev.enabled) })
      + uiField({ id: 'prov-jev-key', label: 'Jev API Key', controlHtml: jevKeyRow })
      + uiField({ id: 'prov-jev-url', label: 'Base URL', controlHtml: `<input id="prov-jev-url" class="pref-input" placeholder="Base URL" value="${esc(jev.base_url || 'https://api.typesafe.ai/v1')}"/>` })
      + uiToggleRow({ id: 'prov-stt-enabled', label: trKey("console.js.panels.providers.stt_label"), checked: stt.enabled !== false })
      + '<div class="console-form-grid">'
      + uiField({ id: 'prov-stt-prov', label: trKey("console.js.panels.providers.stt_provider"), controlHtml: sttProvSelect })
      + uiField({ id: 'prov-stt-lang', label: trKey("console.js.panels.providers.stt_language"), controlHtml: `<input id="prov-stt-lang" class="pref-input" value="${esc(stt.language || 'ko')}"/>` })
      + '</div>'
      + '<div id="prov-cap-stt" class="pref-notice" data-tone="warn" hidden></div>';

    h += uiCard({
      id: 'prov-aux-card',
      icon: 'puzzle',
      title: trKey("console.js.panels.providers.aux_title"),
      bodyHtml: auxBody
    });

    const optsState = _provState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    let saveActionsHtml = uiBtn(trKey("console.js.panels.save_settings"), { id: 'prov-save-btn', variant: 'accent', icon: 'save', attrs: { onclick: 'saveProviderSettings()' } });
    if (canClose) {
      saveActionsHtml += uiBtn(trKey("common.action.close"), { attrs: { onclick: 'closeProviderManager()' } });
    }
    const saveBody = '<div id="prov-cap-summary" class="pref-notice" role="status" aria-live="polite" hidden></div>' + uiActions(saveActionsHtml);
    h += uiCard({
      id: 'prov-save-card',
      icon: 'save',
      title: trKey("console.js.panels.save_settings"),
      split: false,
      bodyHtml: saveBody
    });

    target.innerHTML = h;
    if (cfg.capabilities) applyProviderCapabilities(cfg.capabilities);
    bindProviderCapabilityEvents();
    enhanceConsoleDom();
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
    target.innerHTML = renderPanelLoadingCard('prov-loading-card', 'bolt', 'console.js.panels.providers.title', 'console.js.panels.loading_settings');
    enhanceConsoleDom();
    try {
      const url = resolveRootUrl(_provState.opts, 'providers');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _provState.config = await r.json();
      renderProviderManagerDOM();
    } catch (e) {
      target.innerHTML = renderPanelErrorCard('prov-error-card', 'bolt', 'console.js.panels.providers.title', 'console.js.panels.load_failed', e);
      enhanceConsoleDom();
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
    const sameSubjectMerge = document.getElementById('limit-same-subject-merge').value;
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
        same_subject_merge: sameSubjectMerge,
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
        same_subject_merge: 'off',
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

    const effortOptions = (cur, includeDefault) => {
      let opts = includeDefault ? `<option value="" ${!cur ? 'selected' : ''}>${esc(trKey("console.js.panels.limits.effort_default"))}</option>` : '';
      for (const lv of ['low', 'medium', 'high']) {
        opts += `<option value="${lv}" ${cur === lv ? 'selected' : ''}>${lv}</option>`;
      }
      return opts;
    };

    let h = uiNotice(`${esc(trKey("console.js.panels.limits.desc"))} <code class="pref-mono">data/ingest_limits.json</code>`, 'info');

    h += uiCard({
      id: 'limit-web-card',
      icon: 'globe',
      title: trKey("console.js.panels.limits.web_title"),
      bodyHtml: uiField({
        id: 'limit-raw-char-budget',
        label: trKey("console.js.panels.limits.raw_budget"),
        hint: trKey("console.js.panels.limits.raw_budget_hint"),
        controlHtml: `<input id="limit-raw-char-budget" type="number" min="0" class="pref-input" value="${esc(lims.raw_char_budget ?? 20000)}"/>`
      })
    });

    const pdfBody = '<div class="console-form-grid">'
      + uiField({ id: 'limit-pdf-max-extract', label: trKey("console.js.panels.limits.pdf_max_extract"), controlHtml: `<input id="limit-pdf-max-extract" type="number" min="0" class="pref-input" value="${esc(lims.pdf_max_extract_chars ?? 50000)}"/>` })
      + uiField({ id: 'limit-pdf-paper-threshold', label: trKey("console.js.panels.limits.pdf_paper_threshold"), controlHtml: `<input id="limit-pdf-paper-threshold" type="number" min="0" class="pref-input" value="${esc(lims.pdf_paper_threshold_chars ?? 15000)}"/>` })
      + '</div>'
      + uiField({
        id: 'limit-pdf-parser',
        label: trKey("console.js.panels.limits.pdf_parser"),
        controlHtml: `<select id="limit-pdf-parser" class="pref-select"><option value="default" selected>${esc(trKey("console.js.panels.limits.pdf_parser_default"))}</option></select>`
      })
      + uiToggleRow({ id: 'limit-pdf-exclude-appendix', label: trKey("console.js.panels.limits.pdf_exclude_appendix"), checked: lims.pdf_exclude_appendix !== false })
      + uiToggleRow({ id: 'limit-pdf-exclude-references', label: trKey("console.js.panels.limits.pdf_exclude_references"), checked: lims.pdf_exclude_references !== false })
      + uiToggleRow({ id: 'limit-pdf-detect-encoding-flaws', label: trKey("console.js.panels.limits.pdf_detect_flaws"), checked: lims.pdf_detect_encoding_flaws !== false })
      + '<div class="console-form-grid">'
      + uiField({ id: 'limit-pdf-paper-effort', label: trKey("console.js.panels.limits.pdf_paper_effort"), controlHtml: `<select id="limit-pdf-paper-effort" class="pref-select">${effortOptions(lims.pdf_paper_effort || 'high', false)}</select>` })
      + uiField({ id: 'limit-pdf-default-effort', label: trKey("console.js.panels.limits.pdf_default_effort"), controlHtml: `<select id="limit-pdf-default-effort" class="pref-select">${effortOptions(lims.pdf_default_effort || '', true)}</select>` })
      + uiField({ id: 'limit-pdf-classifier-effort', label: trKey("console.js.panels.limits.pdf_classify_effort"), controlHtml: `<select id="limit-pdf-classifier-effort" class="pref-select">${effortOptions(lims.pdf_classifier_effort || 'low', false)}</select>` })
      + '</div>';
    h += uiCard({
      id: 'limit-pdf-card',
      icon: 'doc',
      title: trKey("console.js.panels.limits.pdf_title"),
      bodyHtml: pdfBody
    });

    const slicingOpts = `<option value="table-exemption" ${(lims.slicing_strategy || 'table-exemption') === 'table-exemption' ? 'selected' : ''}>${esc(trKey("console.js.panels.limits.slicing_table"))}</option><option value="strict" ${lims.slicing_strategy === 'strict' ? 'selected' : ''}>${esc(trKey("console.js.panels.limits.slicing_strict"))}</option>`;
    const kgBody = '<div class="console-form-grid">'
      + uiField({ id: 'limit-extract-char-budget', label: trKey("console.js.panels.limits.extract_budget"), controlHtml: `<input id="limit-extract-char-budget" type="number" min="0" class="pref-input" value="${esc(lims.extract_char_budget ?? 20000)}"/>` })
      + uiField({ id: 'limit-merged-extract-budget', label: trKey("console.js.panels.limits.merged_budget"), controlHtml: `<input id="limit-merged-extract-budget" type="number" min="0" class="pref-input" value="${esc(lims.merged_extract_char_budget ?? 0)}"/>` })
      + '</div>'
      + uiField({ id: 'limit-slicing-strategy', label: trKey("console.js.panels.limits.slicing"), controlHtml: `<select id="limit-slicing-strategy" class="pref-select">${slicingOpts}</select>` });
    h += uiCard({
      id: 'limit-kg-card',
      icon: 'puzzle',
      title: trKey("console.js.panels.limits.kg_title"),
      bodyHtml: kgBody
    });

    const ctxBody = '<div class="console-form-grid">'
      + uiField({ id: 'limit-embed-budget', label: trKey("console.js.panels.limits.embed_budget"), controlHtml: `<input id="limit-embed-budget" type="number" min="0" class="pref-input" value="${esc(lims.embed_char_budget ?? 8000)}"/>` })
      + uiField({ id: 'limit-research-budget', label: trKey("console.js.panels.limits.research_budget"), controlHtml: `<input id="limit-research-budget" type="number" min="0" class="pref-input" value="${esc(lims.research_context_budget ?? 8000)}"/>` })
      + '</div>'
      + uiField({ id: 'limit-expand-budget', label: trKey("console.js.panels.limits.expand_budget"), controlHtml: `<input id="limit-expand-budget" type="number" min="0" class="pref-input" value="${esc(lims.expand_char_budget ?? 2000)}"/>` });
    h += uiCard({
      id: 'limit-context-card',
      icon: 'compass',
      title: trKey("console.js.panels.limits.context_title"),
      bodyHtml: ctxBody
    });

    const mergeOpts = `<option value="off" ${lims.same_subject_merge !== 'shadow' ? 'selected' : ''}>${esc(trKey("console.js.panels.limits.same_subject_merge_off"))}</option><option value="shadow" ${lims.same_subject_merge === 'shadow' ? 'selected' : ''}>${esc(trKey("console.js.panels.limits.same_subject_merge_shadow"))}</option>`;
    const expandBody = uiToggleRow({ id: 'limit-auto-expand', label: trKey("console.js.panels.limits.auto_expand"), desc: trKey("console.js.panels.limits.auto_expand_hint"), checked: lims.auto_expand !== false })
      + uiField({ id: 'limit-same-subject-merge', label: trKey("console.js.panels.limits.same_subject_merge"), hint: trKey("console.js.panels.limits.same_subject_merge_hint"), controlHtml: `<select id="limit-same-subject-merge" class="pref-select">${mergeOpts}</select>` })
      + '<div class="console-form-grid">'
      + uiField({ id: 'limit-expand-max', label: trKey("console.js.panels.limits.expand_max"), controlHtml: `<input id="limit-expand-max" type="number" min="0" class="pref-input" value="${esc(lims.expand_max ?? 5)}"/>` })
      + uiField({ id: 'limit-watch-interval-days', label: trKey("console.js.panels.limits.watch_interval_days"), controlHtml: `<input id="limit-watch-interval-days" type="number" min="0.01" step="any" class="pref-input" value="${esc(lims.watch_interval_days ?? 1)}"/>` })
      + '</div>'
      + `<p class="hint">${esc(trKey("console.js.panels.limits.watch_interval_hint"))}</p>`;
    h += uiCard({
      id: 'limit-expand-section',
      icon: 'link',
      title: trKey("console.js.panels.limits.expand_title"),
      bodyHtml: expandBody
    });

    const mediaBody = uiField({ id: 'limit-presentation-pdf-bytes', label: trKey("console.js.panels.limits.presentation_pdf_max"), controlHtml: `<input id="limit-presentation-pdf-bytes" type="number" min="1" class="pref-input" value="${esc(lims.presentation_pdf_max_bytes ?? 67108864)}"/>` })
      + '<div class="console-form-grid">'
      + uiField({ id: 'limit-video-max-extract', label: trKey("console.js.panels.limits.video_max_extract"), controlHtml: `<input id="limit-video-max-extract" type="number" min="0" class="pref-input" value="${esc(lims.video_max_extract_chars ?? 200000)}"/>` })
      + uiField({ id: 'limit-video-chunk-sec', label: trKey("console.js.panels.limits.video_chunk"), controlHtml: `<input id="limit-video-chunk-sec" type="number" min="1" class="pref-input" value="${esc(lims.video_chunk_duration_sec ?? 240)}"/>` })
      + '</div>'
      + uiField({ id: 'limit-vlm-max-images', label: trKey("console.js.panels.limits.vlm_max_images"), controlHtml: `<input id="limit-vlm-max-images" type="number" min="0" class="pref-input" value="${esc(lims.vlm_max_images ?? 20)}"/>` });
    h += uiCard({
      id: 'limit-media-card',
      icon: 'film',
      title: trKey("console.js.panels.limits.media_title"),
      bodyHtml: mediaBody
    });

    const optsState = _limitState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    let saveActionsHtml = uiBtn(trKey("console.js.panels.save_settings"), { variant: 'accent', icon: 'save', attrs: { onclick: 'saveLimitSettings()' } });
    saveActionsHtml += uiBtn(trKey("console.js.panels.limits.reset"), { icon: 'restore', attrs: { onclick: 'resetLimitSettings()' } });
    if (canClose) {
      saveActionsHtml += uiBtn(trKey("common.action.close"), { attrs: { onclick: 'closeLimitManager()' } });
    }
    const resultStateAttr = msg ? ` data-state="${isSuccess ? 'ok' : 'error'}"` : '';
    const saveCardBody = uiActions(saveActionsHtml) + `<p id="limit-result" class="result" role="status" aria-live="polite"${resultStateAttr}>${msg ? esc(msg) : ''}</p>`;
    h += uiCard({
      id: 'limit-save-card',
      icon: 'save',
      title: trKey("console.js.panels.save_settings"),
      split: false,
      bodyHtml: saveCardBody
    });

    target.innerHTML = h;
    enhanceConsoleDom();
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
    target.innerHTML = renderPanelLoadingCard('limit-loading-card', 'ruler', 'console.js.panels.limits.title', 'console.js.panels.loading_settings');
    enhanceConsoleDom();
    try {
      const url = resolveRootUrl(_limitState.opts, 'limits');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _limitState.config = await r.json();
      renderLimitManagerDOM();
    } catch (e) {
      target.innerHTML = renderPanelErrorCard('limit-error-card', 'ruler', 'console.js.panels.limits.title', 'console.js.panels.load_failed', e);
      enhanceConsoleDom();
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
    const knowledge_type = ((document.getElementById('newthemep-knowledge-type') || {}).value || '').trim();
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

    let listHtml = '<div class="theme-manager-list">';
    for (const t of themes) {
      const themeKey = getThemeKey(t);
      const themeIcon = t.icon || '📁';
      const isPub = t.is_public !== false;
      const pubBadge = uiPill(isPub ? trKey("console.js.panels.themes.public") : trKey("console.js.panels.themes.private"), isPub ? 'ok' : 'warn');
      const toggleBtn = uiBtn(isPub ? trKey("console.js.panels.themes.make_private") : trKey("console.js.panels.themes.make_public"), { sm: true, attrs: { onclick: `toggleThemeVisibility(${jsArg(themeKey)}, ${!isPub})` } });
      const isPres = t.presentation_enabled === true;
      const presBadge = `<span class="pref-pill" data-tone="${isPres ? 'accent' : 'neutral'}" title="${esc(trKey(isPres ? 'console.js.panels.themes.presentation_on_title' : 'console.js.panels.themes.presentation_off_title'))}"><span data-icon="${isPres ? 'screen' : 'block'}"></span> ${esc(trKey(isPres ? 'console.js.panels.themes.presentation_on' : 'console.js.panels.themes.presentation_off'))}</span>`;
      const togglePresBtn = uiBtn(isPres ? trKey("console.js.panels.themes.presentation_turn_off") : trKey("console.js.panels.themes.presentation_turn_on"), { sm: true, attrs: { onclick: `toggleThemePresentation(${jsArg(themeKey)}, ${!isPres})` } });
      const idLabel = themeKey === '0' ? trKey("console.js.panels.themes.id_default") : trKey("console.js.panels.themes.id_label", { id: t.number ? t.number : '#' + t.id });
      const rawMgr = (t.sorcerer || t.knowledge_manager || '').trim();
      const effMgr = rawMgr || (t.effective_sorcerer || defaultMgr);
      const legacyHostsBadge = (t.legacy_hosts && t.legacy_hosts.length) ? `<span class="pref-pill" data-tone="ap" title="${esc(trKey("console.js.panels.themes.legacy_title"))}"><span data-icon="globe"></span> ${esc(trKey("console.js.panels.themes.legacy_count", { count: t.legacy_hosts.length }))}</span>` : '';

      let actions = legacyHostsBadge;
      if (t.ga_measurement_id) {
        actions += `<span class="pref-pill" data-tone="warn" title="Google Analytics 4"><span data-icon="chart"></span> ${esc(t.ga_measurement_id)}</span>`;
      }
      actions += pubBadge;
      if (isOwner) actions += toggleBtn;
      actions += presBadge;
      if (isOwner) actions += togglePresBtn;
      if (isOwner) actions += uiBtn('', { sm: true, labelHtml: `<span data-icon="edit"></span> ${esc(trKey("console.js.panels.themes.edit"))}`, attrs: { onclick: `showThemeEditForm(${jsArg(themeKey)})` } });
      actions += uiBtn('', { sm: true, labelHtml: `<span data-icon="settings"></span> ${esc(trKey("console.js.panels.themes.show_options"))}`, attrs: { onclick: `toggleThemeOptionsView(${jsArg(themeKey)})` } });
      if (isAdmin && themeKey !== '0') {
        actions += uiBtn('', { variant: 'danger', sm: true, labelHtml: `<span data-icon="trash"></span> ${esc(trKey("common.action.delete"))}`, attrs: { onclick: `deleteThemeFromUI(${jsArg(themeKey)}, ${jsArg(t.label)})` } });
      }

      listHtml += '<div class="pref-row" style="flex-direction:column;align-items:stretch;gap:0.5rem;">';
      listHtml += '<div class="console-card-head">';
      listHtml += `<strong>${esc(themeIcon)} ${esc(t.label)} <small class="hint">(${esc(idLabel)})</small></strong>`;
      listHtml += uiActions(actions);
      listHtml += '</div>';
      if (t.description) listHtml += `<p class="hint">${esc(t.description)}</p>`;
      listHtml += `<p class="hint">${esc(trKey("console.js.panels.themes.manager_prefix"))} ${esc(effMgr)}</p>`;

      listHtml += `<div id="theme-options-view-${themeKey}" class="pref-notice" data-tone="info" style="display:none;flex-direction:column;gap:0.25rem;">`;
      listHtml += `<div><strong><span data-icon="settings"></span> ${esc(trKey("console.js.panels.themes.options_title"))}</strong></div>`;
      listHtml += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_label"))}</b>: ${esc(t.label)}</div>`;
      listHtml += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_manager"))}</b>: ${esc(effMgr)}</div>`;
      listHtml += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_icon"))}</b>: ${esc(themeIcon)}</div>`;
      listHtml += `<div>• <b>${esc(trKey("console.js.panels.col_description"))}</b>: ${esc(t.description || trKey("console.js.panels.themes.no_description"))}</div>`;
      listHtml += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_legacy"))}</b>: ${(t.legacy_hosts && t.legacy_hosts.length) ? t.legacy_hosts.map(lh => esc(trKey("console.js.panels.themes.legacy_host", { host: lh.host, activated: lh.activated_at || '-', sunset: lh.sunset_at || '-' }))).join(', ') : esc(trKey("console.js.panels.themes.none"))}</div>`;
      listHtml += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_ga"))}</b>: ${esc(t.ga_measurement_id || trKey("console.js.panels.themes.ga_unset"))}</div>`;
      listHtml += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_visibility"))}</b>: ${isPub ? esc(trKey("console.js.panels.themes.visibility_public")) : esc(trKey("console.js.panels.themes.private"))}</div>`;
      listHtml += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_presentation"))}</b>: ${uiPill(trKey(isPres ? "console.js.panels.themes.presentation_enabled" : "console.js.panels.themes.presentation_disabled"), isPres ? 'accent' : 'neutral')}</div>`;
      if (t.stats) listHtml += `<div>• <b>${esc(trKey("console.js.panels.themes.opt_stats"))}</b>: ${esc(trKey("console.js.panels.themes.stats", { documents: t.stats.documents || 0, entities: t.stats.entities || 0, relations: t.stats.relations || 0 }))}</div>`;
      listHtml += '</div>';

      if (isOwner) {
        listHtml += `<div id="theme-edit-form-${themeKey}" style="display:none;margin-top:0.25rem;padding-top:0.625rem;border-top:1px dashed var(--border);flex-direction:column;gap:0.625rem;">`;
        listHtml += '<div class="console-form-grid">';
        listHtml += uiField({ id: `editthemep-label-${themeKey}`, label: trKey("console.js.panels.themes.opt_label"), controlHtml: `<input id="editthemep-label-${themeKey}" class="pref-input" value="${esc(t.label)}"/>` });
        listHtml += uiField({ id: `editthemep-sorcerer-${themeKey}`, label: trKey("console.js.panels.themes.opt_manager"), controlHtml: `<input id="editthemep-sorcerer-${themeKey}" class="pref-input" placeholder="${esc(trKey("console.js.panels.themes.opt_manager"))}" value="${esc(rawMgr)}"/>` });
        listHtml += '</div>';
        listHtml += uiField({ id: `editthemep-desc-${themeKey}`, label: trKey("console.js.panels.col_description"), controlHtml: `<input id="editthemep-desc-${themeKey}" class="pref-input" value="${esc(t.description || '')}"/>` });
        if (t.legacy_hosts && t.legacy_hosts.length) {
          listHtml += uiNotice(`<span data-icon="globe"></span> ${esc(trKey("console.js.panels.themes.legacy_note"))} ${t.legacy_hosts.map(lh => esc(lh.host)).join(', ')}`, 'info');
        }
        listHtml += '<div class="console-form-grid">';
        listHtml += uiField({ id: `editthemep-ga-${themeKey}`, label: trKey("console.js.panels.themes.ga_label"), controlHtml: `<input id="editthemep-ga-${themeKey}" class="pref-input" placeholder="${esc(trKey("console.js.panels.themes.ga_placeholder"))}" value="${esc(t.ga_measurement_id || '')}"/>` });
        listHtml += uiField({ id: `editthemep-icon-${themeKey}`, label: trKey("console.js.panels.themes.opt_icon"), controlHtml: `<input id="editthemep-icon-${themeKey}" class="pref-input" value="${esc(themeIcon)}"/>` });
        listHtml += '</div>';
        listHtml += uiToggleRow({ id: `editthemep-pub-${themeKey}`, label: trKey("console.js.panels.themes.public_checkbox"), checked: isPub });
        listHtml += uiToggleRow({ id: `editthemep-pres-${themeKey}`, label: trKey("console.js.panels.themes.presentation_checkbox"), checked: isPres });
        let formBtns = uiBtn(trKey("common.action.save"), { variant: 'accent', sm: true, attrs: { onclick: `updateThemeFromUI(${jsArg(themeKey)})` } });
        formBtns += uiBtn(trKey("common.action.cancel"), { sm: true, attrs: { onclick: `hideThemeEditForm(${jsArg(themeKey)})` } });
        if (isAdmin && themeKey !== '0') {
          formBtns += uiBtn('', { variant: 'danger', sm: true, labelHtml: `<span data-icon="trash"></span> ${esc(trKey("console.js.panels.themes.delete_full"))}`, attrs: { style: 'margin-left:auto;', onclick: `deleteThemeFromUI(${jsArg(themeKey)}, ${jsArg(t.label)})` } });
        }
        listHtml += uiActions(formBtns);
        listHtml += '</div>';
      }
      listHtml += '</div>';
    }
    listHtml += '</div>';

    let h = uiCard({
      id: 'theme-list-card',
      icon: 'folder',
      title: trKey("console.js.panels.themes.title"),
      hint: isOwner ? trKey("console.js.panels.themes.desc_owner") : trKey("console.js.panels.themes.desc_reader"),
      split: false,
      bodyHtml: listHtml
    });

    if (isOwner) {
      const kTypeOpts = `<option value="real">${esc(trKey("kb.knowledge_type.real"))}</option><option value="fictional">${esc(trKey("kb.knowledge_type.fictional"))}</option>`;
      let addForm = '<details class="pref-disclosure"><summary><span data-icon="plus"></span> ' + esc(trKey("console.js.panels.themes.add_title")) + '</summary>';
      addForm += '<div style="display:flex;flex-direction:column;gap:0.625rem;margin-top:0.625rem;">';
      addForm += '<div class="console-form-grid">';
      addForm += uiField({ id: 'newthemep-label', label: trKey("console.js.panels.themes.opt_label"), controlHtml: `<input id="newthemep-label" class="pref-input" placeholder="${esc(trKey("console.js.panels.themes.label_placeholder"))}"/>` });
      addForm += uiField({ id: 'newthemep-knowledge-type', label: trKey("kb.knowledge_type.label"), controlHtml: `<select id="newthemep-knowledge-type" class="pref-select">${kTypeOpts}</select>` });
      addForm += '</div>';
      addForm += '<div class="console-form-grid">';
      addForm += uiField({ id: 'newthemep-sorcerer', label: trKey("console.js.panels.themes.opt_manager"), controlHtml: `<input id="newthemep-sorcerer" class="pref-input" placeholder="${esc(trKey("console.js.panels.themes.opt_manager"))}"/>` });
      addForm += uiField({ id: 'newthemep-desc', label: trKey("console.js.panels.col_description"), controlHtml: `<input id="newthemep-desc" class="pref-input" placeholder="${esc(trKey("console.js.panels.themes.desc_placeholder"))}"/>` });
      addForm += '</div>';
      addForm += '<div class="console-form-grid">';
      addForm += uiField({ id: 'newthemep-ga', label: trKey("console.js.panels.themes.ga_label"), controlHtml: `<input id="newthemep-ga" class="pref-input" placeholder="${esc(trKey("console.js.panels.themes.ga_placeholder"))}"/>` });
      addForm += uiField({ id: 'newthemep-icon', label: trKey("console.js.panels.themes.opt_icon"), controlHtml: '<input id="newthemep-icon" class="pref-input" value="📁"/>' });
      addForm += '</div>';
      addForm += uiToggleRow({ id: 'newthemep-pres', label: trKey("console.js.panels.themes.presentation_checkbox"), checked: false });
      addForm += uiActions(uiBtn(trKey("console.js.panels.themes.create"), { variant: 'accent', attrs: { onclick: 'createThemeFromUI()' } }));
      addForm += '</div></details>';
      h += uiCard({
        id: 'theme-create-card',
        icon: 'plus',
        title: trKey("console.js.panels.themes.add_title"),
        split: false,
        bodyHtml: addForm
      });
    } else {
      h += uiNotice(`<span data-icon="info"></span> ${esc(trKey("console.js.panels.themes.ask_owner"))}`, 'info');
    }

    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    if (canClose) {
      h += uiActions(uiBtn(trKey("common.action.close"), { attrs: { onclick: 'closeThemeManager()' } }));
    }

    target.innerHTML = h;
    enhanceConsoleDom();
  }

  async function renderThemeManager(container, opts) {
    if (container instanceof HTMLElement || (container && container.nodeType === 1) || (typeof container === 'string' && (container.startsWith('#') || container.startsWith('.')))) {
      _themeState.container = resolveContainer(container);
      _themeState.opts = opts || {};
    }
    const target = _themeState.container;
    if (!target) return;
    target.innerHTML = renderPanelLoadingCard('theme-loading-card', 'folder', 'console.js.panels.themes.title', 'console.js.panels.themes.loading');
    enhanceConsoleDom();
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
      target.innerHTML = renderPanelErrorCard('theme-error-card', 'folder', 'console.js.panels.themes.title', 'console.js.panels.themes.load_failed', e);
      enhanceConsoleDom();
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
    const retiredFallback = 'Manual persistent tokens are retired. New token creation is disabled; connect clients via OAuth 2.1 in /account/mcp.';
    if (_tokenState.retired) {
      renderTokenManagerDOM(trFallback("console.js.panels.tokens.retired_notice", retiredFallback), false);
      return;
    }
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
        if (r.status === 410) {
          _tokenState.retired = true;
          renderTokenManagerDOM(err.detail || trFallback("console.js.panels.tokens.retired_notice", retiredFallback), false);
          return;
        }
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
    const isRetired = Boolean(_tokenState.retired);

    let h = '';
    if (isRetired) {
      const retiredFallback = 'Manual persistent tokens are retired. New token creation is disabled; connect clients via OAuth 2.1 in /account/mcp.';
      const retiredText = trFallback("console.js.panels.tokens.retired_notice", retiredFallback);
      const retiredHtml = retiredText.indexOf('/account/mcp') !== -1 ? esc(retiredText).replace('/account/mcp', '<a href="/account/mcp">/account/mcp</a>') : (esc(retiredText) + ' <a href="/account/mcp">/account/mcp</a>');
      h += uiNotice(`<span data-icon="warning"></span> ${retiredHtml}`, 'danger', '', { id: 'tokens-retired-banner', 'data-tokens-retired': 'true', role: 'status' });
    }
    const scopeNotices = _tokenState.scopeNotices || [];
    if (scopeNotices.length) {
      const scopeListHtml = '<ul>' + scopeNotices.map(n => `<li>${esc(n.name)} (#${esc(String(n.id))})</li>`).join('') + '</ul>';
      h += uiNotice(`<span data-icon="warning"></span> ${esc(trKey("console.js.panels.tokens.scope_notice"))}${scopeListHtml}`, 'warn', '', { role: 'status' });
    }
    h += renderAlertBanner(msg, isSuccess);

    if (activeNewToken) {
      const rawVal = activeNewToken.token || '';
      const mcpOrigin = window.location.origin;
      const mcpUrl = `${mcpOrigin}/mcp`;
      const mcpSnippet = JSON.stringify({ mcpServers: { 'claire-bible': { type: 'http', url: mcpUrl, headers: { Authorization: `Bearer ${rawVal}` } } } }, null, 2);
      const mintedBody = `<div class="console-form-row"><input type="text" id="newly-minted-token" class="pref-input pref-mono" readonly value="${esc(rawVal)}" style="flex:1;min-width:12rem;" onclick="this.select();"/>${uiBtn(trKey("console.js.panels.tokens.copy"), { variant: 'accent', icon: 'clipboard', attrs: { onclick: 'copyMintedToken()' } })}</div>`
        + `<details class="pref-disclosure"><summary><span data-icon="clipboard"></span> ${esc(trKey("console.js.panels.tokens.mcp_json"))}</summary><pre class="pref-code">${esc(mcpSnippet)}</pre></details>`;
      h += uiCard({
        id: 'tokens-minted-card',
        icon: 'check-circle',
        title: trKey("console.js.panels.tokens.minted_title"),
        hint: trKey("console.js.panels.tokens.minted_hint"),
        split: false,
        bodyHtml: mintedBody
      });
    }

    const disabledAttr = isRetired ? ' disabled aria-disabled="true"' : '';
    const scopeOptsHtml = `<option value="readonly" selected>${esc(trKey("console.js.panels.tokens.scope_readonly"))}</option><option value="write">${esc(trKey("console.js.panels.tokens.scope_write"))}</option><option value="owner">${esc(trKey("console.js.panels.tokens.scope_owner"))}</option>`;
    const expiryOptsHtml = `<option value="0" selected>${esc(trKey("console.js.panels.tokens.expiry_none"))}</option><option value="30">${esc(trKey("console.js.panels.tokens.expiry_30"))}</option><option value="90">${esc(trKey("console.js.panels.tokens.expiry_90"))}</option><option value="365">${esc(trKey("console.js.panels.tokens.expiry_365"))}</option>`;
    const createBody = uiField({ id: 'new-token-name', label: trKey("console.js.panels.tokens.name_label"), controlHtml: `<input type="text" id="new-token-name" class="pref-input"${disabledAttr} placeholder="${esc(trKey("console.js.panels.tokens.name_placeholder"))}"/>` })
      + '<div class="console-form-grid">'
      + uiField({ id: 'new-token-scope', label: trKey("console.js.panels.tokens.scope_label"), controlHtml: `<select id="new-token-scope" class="pref-select"${disabledAttr}>${scopeOptsHtml}</select>` })
      + uiField({ id: 'new-token-expiry', label: trKey("console.js.panels.tokens.expiry_label"), controlHtml: `<select id="new-token-expiry" class="pref-select"${disabledAttr}>${expiryOptsHtml}</select>` })
      + '</div>'
      + uiActions(uiBtn(trKey("console.js.panels.tokens.issue"), { id: 'create-api-token-btn', variant: 'accent', icon: 'key', disabled: isRetired, attrs: { onclick: 'createApiToken()' } }));
    h += uiCard({
      id: 'tokens-create-card',
      icon: 'plus',
      title: trKey("console.js.panels.tokens.new_title"),
      hint: trKey("console.js.panels.tokens.desc"),
      attrs: isRetired ? { style: 'opacity:0.65;' } : null,
      bodyHtml: createBody
    });

    let listBody = '';
    if (!tokens.length) {
      listBody = `<p class="hint">${esc(trKey("console.js.panels.tokens.empty"))}</p>`;
    } else {
      const scopeTones = { owner: 'accent', write: 'ap', readonly: 'neutral' };
      const nowTs = Date.now() / 1000;
      let rowsHtml = '';
      for (const tok of tokens) {
        const isRevoked = tok.revoked_at !== null && tok.revoked_at !== undefined;
        const isExpired = tok.expires_at && tok.expires_at < nowTs;
        let statusTone = 'ok';
        let statusText = trKey("console.js.panels.tokens.status_active");
        if (isRevoked) {
          statusTone = 'danger';
          statusText = trKey("console.js.panels.tokens.status_revoked");
        } else if (isExpired) {
          statusTone = 'warn';
          statusText = trKey("console.js.panels.tokens.status_expired");
        }

        let expStr = trKey("console.js.panels.tokens.expiry_none_short");
        if (tok.expires_at) {
          const d = new Date(tok.expires_at * 1000);
          expStr = d.toLocaleDateString() + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }

        let lastUsedStr = trKey("console.js.panels.tokens.never_used");
        if (tok.last_used_at) {
          const lu = new Date(tok.last_used_at * 1000);
          lastUsedStr = lu.toLocaleDateString() + ' ' + lu.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        }

        const revokeHtml = isRevoked ? '' : uiBtn(trKey("console.js.panels.tokens.revoke"), { variant: 'danger', sm: true, attrs: { onclick: `revokeApiToken(${tok.id}, ${jsArg(tok.name)})` } });
        rowsHtml += `<tr${isRevoked ? ' data-off="true"' : ''}>`;
        rowsHtml += `<td><strong>#${tok.id} ${esc(tok.name)}</strong></td>`;
        rowsHtml += `<td>${uiPill(tok.scope, scopeTones[tok.scope] || 'neutral')}</td>`;
        rowsHtml += `<td>${uiPill(statusText, statusTone)}</td>`;
        rowsHtml += `<td><code class="pref-mono">${esc(tok.token_masked || 'cb_...')}</code></td>`;
        rowsHtml += `<td>${esc(expStr)}</td>`;
        rowsHtml += `<td>${esc(lastUsedStr)}</td>`;
        rowsHtml += `<td>${revokeHtml}</td>`;
        rowsHtml += '</tr>';
      }
      const idHeader = trKey("console.js.panels.tokens.identifier").replace(/:+\s*$/, '');
      listBody = uiTable({
        headers: [
          trKey("console.js.panels.tokens.name_label"),
          trKey("console.js.panels.tokens.scope_label"),
          trKey("console.js.panels.col_status"),
          idHeader,
          trKey("console.js.panels.tokens.expires"),
          trKey("console.js.panels.tokens.last_used"),
          ''
        ],
        bodyHtml: rowsHtml
      });
    }
    h += uiCard({
      id: 'tokens-list-card',
      icon: 'clipboard',
      title: trKey("console.js.panels.tokens.list_title", { count: tokens.length }),
      split: false,
      bodyHtml: listBody
    });

    const optsState = _tokenState.opts || {};
    const canClose = optsState.showClose !== false && optsState.showCloseButton !== false && (typeof optsState.onClose === 'function' || typeof window.closeDrawer === 'function');
    if (canClose) {
      h += uiActions(uiBtn(trKey("common.action.close"), { attrs: { onclick: 'closeTokenManager()' } }));
    }

    target.innerHTML = h;
    enhanceConsoleDom();
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
    target.innerHTML = renderPanelLoadingCard('tokens-loading-card', 'key', 'console.js.panels.tokens.title', 'console.js.panels.tokens.loading');
    enhanceConsoleDom();
    _tokenState.retired = false;
    _tokenState.deprecationNotice = '';
    try {
      const url = resolveRootUrl(_tokenState.opts, 'tokens');
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      _tokenState.tokens = data.tokens || [];
      _tokenState.retired = Boolean(data.retired);
      _tokenState.deprecationNotice = data.deprecation_notice || '';
      // The server sends each scope notice once (design Section 6.6 rule 2). Keep it for the next renders of this page.
      if (Array.isArray(data.scope_notices) && data.scope_notices.length) _tokenState.scopeNotices = data.scope_notices;
      const initialMsg = (_tokenState.opts && _tokenState.opts.initialMsg) || undefined;
      const initialSuccess = (_tokenState.opts && _tokenState.opts.initialSuccess) || false;
      renderTokenManagerDOM(initialMsg, initialSuccess, _tokenState.newCreatedToken);
    } catch (e) {
      target.innerHTML = renderPanelErrorCard('tokens-error-card', 'key', 'console.js.panels.tokens.title', 'console.js.panels.tokens.load_failed', e);
      enhanceConsoleDom();
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
