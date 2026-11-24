/**
 * Claire Shared Management Panels Controller (manage_panels.js)
 * Reusable DOM and API controllers for Provider, Limit, and Theme management.
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

  const _provState = { container: null, opts: {}, config: null, activeTab: 'status' };
  const _limitState = { container: null, opts: {}, config: null };
  const _themeState = { container: null, opts: {}, themes: [] };

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
  // 1. PROVIDER MANAGER (/admin/providers, PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md)
  // ==========================================
  // What applies now (read-only), then one form for each part: the key, the models, STT, and the advanced settings. Each form saves only its part with PATCH /providers and shows the result next to its buttons; the inputs stay after a failure.

  function togglePassVisibility(id) {
    const el = document.getElementById(id);
    if (!el) return;
    el.type = el.type === 'password' ? 'text' : 'password';
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

  function modelIds(rawList) {
    const src = Array.isArray(rawList) && rawList.length ? rawList : fallbackGeminiVariants();
    return src.map(item => (typeof item === 'string' ? item : String((item && item.id) || ''))).filter(Boolean);
  }

  // A select of models. ``emptyLabel`` adds the first option "" (follow the default). A current value that is not in the list stays as an option, so a save does not change it silently.
  function modelSelectHtml(id, ids, current, emptyLabel) {
    const cur = String(current || '');
    let html = `<select id="${esc(id)}" name="${esc(id)}" class="pref-select">`;
    if (emptyLabel != null) html += `<option value=""${cur === '' ? ' selected' : ''}>${esc(emptyLabel)}</option>`;
    const list = ids.slice();
    if (cur && list.indexOf(cur) === -1) list.unshift(cur);
    for (const m of list) html += `<option value="${esc(m)}"${m === cur ? ' selected' : ''}>${esc(m)}</option>`;
    return html + '</select>';
  }

  const PROVIDER_SLOTS = ['extract_graph', 'summarize', 'render_detail', 'entity_judge', 'relation_judge', 'classify', 'research'];

  function nowStamp() {
    const d = new Date();
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  // The status of one form, next to its buttons (role="status").
  function setFormStatus(form, state, text) {
    const out = form ? form.querySelector('[data-prov-status]') : null;
    if (!out) return;
    out.textContent = text || '';
    if (state) out.setAttribute('data-state', state);
    else out.removeAttribute('data-state');
  }

  function statePill(state) {
    const map = {
      ok: ['ok', 'console.js.panels.providers.state.ok'],
      on: ['ok', 'console.js.panels.providers.state.on'],
      off: ['neutral', 'console.js.panels.providers.state.off'],
      mock: ['warn', 'console.js.panels.providers.state.mock'],
      broken: ['danger', 'console.js.panels.providers.state.broken']
    };
    const m = map[state] || map.off;
    return uiPill(trKey(m[1]), m[0]);
  }

  function slotLabel(slot) {
    return trKey(`console.js.panels.providers.view.slot.${slot}`);
  }

  function renderEffectiveCardHtml(cfg) {
    const eff = cfg.effective || {};
    const tier = cfg.server_tier || { provider: 'none' };
    const effProv = String(eff.effective_provider || '');
    const textState = effProv === 'mock' ? 'mock' : (effProv === 'unavailable' || effProv === '' ? 'broken' : 'ok');
    const slots = Array.isArray(eff.slots) ? eff.slots : [];
    const overrides = slots.filter(s => s.overridden);
    const code = v => `<code class="pref-mono">${esc(v || '-')}</code>`;
    const providerName = effProv === 'gemini' ? 'Google AI Studio ' : (effProv === 'mock' ? `${trKey('console.js.panels.providers.mock_name')} ` : '');
    let textSub = '';
    if (overrides.length) {
      textSub = `<div class="hint">${esc(trKey('console.js.panels.providers.eff.overrides', { count: overrides.length }))} ${overrides.map(s => `${esc(slotLabel(s.slot))} → ${code(s.model)}`).join(', ')}</div>`;
    }
    const stt = eff.stt || {};
    const sttState = !stt.enabled ? 'off' : (stt.ready ? 'on' : 'broken');
    const vlm = eff.vlm || {};
    const vlmState = !vlm.enabled ? 'off' : (vlm.ready ? 'on' : 'broken');
    const tierText = (tier.provider || 'none') === 'none'
      ? trKey('console.js.panels.providers.eff.members_none')
      : trKey('console.js.panels.providers.eff.members_tier', { model: tier.model || eff.model || '-', effort: tier.max_effort || '-' });
    const rows = [
      ['text', trKey('console.js.panels.providers.view.feature.text') + ' · ' + slotLabel('research'), `${esc(providerName)}${code(eff.model)}${textSub}`, statePill(textState)],
      ['embed', trKey('console.js.panels.providers.eff.embed'), `${code(eff.embed_model)}<div class="hint">${esc(trKey('console.js.panels.providers.eff.embed_sub'))}</div>`, statePill(eff.api_key_set || effProv === 'mock' ? 'ok' : 'broken')],
      ['stt', trKey('console.js.panels.providers.view.feature.stt'), `Google AI Studio ${code(stt.model)} ${uiPill(trKey('console.js.panels.providers.fixed'), 'neutral')}<div class="hint">${esc(trKey('console.js.panels.providers.eff.stt_sub', { language: sttLanguageText(stt.order) }))}</div>`, statePill(sttState)],
      ['vlm', trKey('console.js.panels.providers.view.feature.vlm'), `${code(vlm.model)}<div class="hint">${esc(trKey('console.js.panels.providers.eff.vlm_sub'))}</div>`, statePill(vlmState)],
      ['jev', trKey('console.js.panels.providers.eff.jev'), esc(trKey('console.js.panels.providers.eff.jev_sub')), statePill(eff.jev_enabled ? 'on' : 'off')],
      ['members', trKey('console.js.panels.providers.eff.members'), esc(tierText), `<a href="/admin/server#server-tier">${esc(trKey('console.js.panels.providers.eff.members_edit'))}</a>`]
    ];
    const body = rows.map(r => `<tr data-prov-effective="${esc(r[0])}"><td>${esc(r[1])}</td><td>${r[2]}</td><td>${r[3]}</td></tr>`).join('');
    let h = uiTable({ headers: [trKey('console.js.panels.providers.view.col.feature'), trKey('console.js.panels.providers.view.col.ai'), trKey('console.js.panels.col_status')], bodyHtml: body });
    const problems = Array.isArray(eff.problems) ? eff.problems : [];
    if (problems.length) {
      h += uiNotice(`${uiIcon('warning')} ${problems.map(p => esc(trKey(`console.js.panels.providers.problem.${p}`))).join('<br>')}`, 'danger', '', { id: 'prov-problems', role: 'status' });
    }
    const recent = ((cfg.lineage_summary || {}).recent_documents || []).slice(0, 5);
    if (recent.length) {
      const recRows = recent.map(d => {
        const finals = ((d.lineage || {}).final_output_models) || {};
        const tags = Object.entries(finals).map(([st, info]) => `${st}=${(info && info.provider) || '?'}:${(info && info.model) || '?'}`).join(', ');
        return `<tr><td>${esc(d.title || d.id)}</td><td><code class="pref-mono">${esc(tags || '-')}</code></td></tr>`;
      }).join('');
      h += `<details class="pref-disclosure" id="prov-recent-models"><summary>${esc(trKey('console.js.panels.providers.eff.recent', { count: recent.length }))}</summary>${uiTable({ headers: [trKey('console.js.panels.lineage.col_document'), trKey('console.js.panels.lineage.col_models')], bodyHtml: recRows })}</details>`;
    }
    h += `<p class="hint"><a href="/support/pipeline" data-prov-pipeline-link>${esc(trKey('console.js.panels.providers.eff.pipeline'))}</a></p>`;
    const badge = uiPill(trKey(problems.length ? 'console.js.panels.providers.eff.badge_problem' : 'console.js.panels.providers.eff.badge_ok'), problems.length ? 'danger' : 'ok');
    return uiCard({ id: 'prov-effective-card', icon: 'check-circle', title: trKey('console.js.panels.providers.eff.title'), badgeHtml: badge, hint: trKey('console.js.panels.providers.eff.hint'), split: false, bodyHtml: h });
  }

  function actionsWithStatus(buttonsHtml) {
    return uiActions(buttonsHtml + '<span class="result" data-prov-status role="status" aria-live="polite"></span>');
  }

  function submitBtn(label, value, variant, extra) {
    return `<button type="submit" name="action" value="${esc(value)}" class="${esc(joinCls('pref-btn', variant === 'accent' ? 'pref-btn-accent' : (variant === 'danger' ? 'pref-btn-danger' : '')))}"${extra || ''}>${esc(label)}</button>`;
  }

  function renderKeyFormHtml(cfg) {
    const gem = ((cfg.providers || {}).gemini) || {};
    const has = Boolean(gem.has_api_key);
    const badge = uiPill(trKey(has ? 'console.js.panels.providers.key.set' : 'console.js.panels.providers.key.unset'), has ? 'ok' : 'danger', '', { 'data-prov-key-state': has ? 'set' : 'unset' });
    const keyRow = `<div class="console-form-row"><input id="prov-gemini-key" name="prov-gemini-key" type="password" class="pref-input" style="flex:1;min-width:12rem;" autocomplete="off" spellcheck="false" placeholder="${esc(trKey(has ? 'console.js.panels.providers.key.placeholder_set' : 'console.js.panels.providers.key.placeholder_unset'))}"/>${renderTogglePassBtn('prov-gemini-key')}</div>`;
    const buttons = submitBtn(trKey('console.js.panels.providers.key.save'), 'save', 'accent')
      + submitBtn(trKey('console.js.panels.providers.key.check'), 'check', '', ' formnovalidate')
      + (has ? submitBtn(trKey('console.js.panels.providers.key.delete'), 'delete', 'danger', ' formnovalidate') : '');
    return uiCard({
      tag: 'form', id: 'prov-key-form', icon: 'lock', title: trKey('console.js.panels.providers.key.title'), badgeHtml: badge,
      hint: trKey('console.js.panels.providers.key.hint'), split: false, attrs: { 'data-prov-form': 'key', novalidate: true },
      bodyHtml: uiField({ id: 'prov-gemini-key', label: trKey('console.js.panels.providers.key.label'), controlHtml: keyRow }) + actionsWithStatus(buttons)
    });
  }

  function renderModelFormHtml(cfg) {
    const gem = ((cfg.providers || {}).gemini) || {};
    const ids = modelIds(gem.available_models);
    const routing = cfg.feature_routing || {};
    const embedIds = Array.from(new Set(['gemini-embedding-2', 'gemini-embedding-001'].concat((gem.available_embed_models || []).map(x => (typeof x === 'string' ? x : String((x && x.id) || '')))).filter(x => x && x !== 'gemini-embedding-2-preview')));
    const embedLabel = m => (m === 'gemini-embedding-2' ? `${m} (${trKey('console.js.panels.providers.model.embed_recommended')})` : (m === 'gemini-embedding-001' ? `${m} (${trKey('console.js.panels.providers.model.embed_eos')})` : m));
    const curEmbed = String(gem.embed_model || 'gemini-embedding-2');
    let embedSel = '<select id="prov-gemini-embed-model" name="prov-gemini-embed-model" class="pref-select">';
    for (const m of embedIds) embedSel += `<option value="${esc(m)}"${m === curEmbed ? ' selected' : ''}>${esc(embedLabel(m))}</option>`;
    embedSel += '</select>';
    let slotRows = '';
    for (const slot of PROVIDER_SLOTS) {
      const r = routing[slot] || {};
      slotRows += `<tr data-route-slot="${esc(slot)}"><td><label for="prov-route-${esc(slot)}-model">${esc(slotLabel(slot))}</label></td><td>${modelSelectHtml(`prov-route-${slot}-model`, ids, r.model || '', trKey('console.js.panels.providers.model.follow_default'))}</td></tr>`;
    }
    const routingHtml = `<details class="pref-disclosure" id="prov-routing"><summary>${esc(trKey('console.js.panels.providers.model.routing_title'))}</summary><p class="hint">${esc(trKey('console.js.panels.providers.model.routing_hint'))}</p>${uiTable({ headers: [trKey('console.js.panels.providers.view.col.feature'), trKey('console.js.panels.col_model')], bodyHtml: slotRows })}</details>`;
    return uiCard({
      tag: 'form', id: 'prov-model-form', icon: 'bolt', title: trKey('console.js.panels.providers.model.title'), split: false, attrs: { 'data-prov-form': 'model' },
      bodyHtml: uiField({ id: 'prov-gemini-model', label: trKey('console.js.panels.providers.model.main'), controlHtml: modelSelectHtml('prov-gemini-model', ids, gem.model || '', null), hint: trKey('console.js.panels.providers.model.main_hint') })
        + uiField({ id: 'prov-gemini-embed-model', label: trKey('console.js.panels.providers.model.embed'), controlHtml: embedSel, hint: trKey('console.js.panels.providers.model.embed_hint') })
        + routingHtml
        + actionsWithStatus(submitBtn(trKey('console.js.panels.providers.model.save'), 'save', 'accent'))
    });
  }

  // The language order of a video (accounts/provider_view.media_language_order): publisher captions, then automatic captions, each in the order of the preferred languages; STT only when no caption is usable.
  function sttLanguageText(order) {
    const o = order || {};
    return String(o.stt_language || 'auto').toLowerCase() === 'auto'
      ? trKey('console.js.panels.providers.stt.order_hints', { hints: (o.stt_hints || []).join(', ') || '-' })
      : trKey('console.js.panels.providers.stt.order_language', { language: o.stt_language || '-' });
  }

  function renderSttOrderHtml(cfg) {
    const order = (((cfg.effective || {}).stt || {}).order) || {};
    const langs = Array.isArray(order.caption_languages) ? order.caption_languages : [];
    const chain = langs.map(l => `<code class="pref-mono">${esc(l)}</code>`).join(' → ') || '-';
    return `<div id="prov-stt-order"><h3>${esc(trKey('console.js.panels.providers.stt.order_title'))}</h3>`
      + `<ol class="console-steps"><li data-stt-order="manual">${esc(trKey('console.js.panels.providers.stt.order_manual'))}: ${chain}</li>`
      + `<li data-stt-order="automatic">${esc(trKey('console.js.panels.providers.stt.order_automatic'))}: ${chain}</li>`
      + `<li data-stt-order="stt">${esc(trKey('console.js.panels.providers.stt.order_stt'))} · ${esc(sttLanguageText(order))}</li></ol>`
      + `<p class="hint">${esc(trKey('console.js.admin.providers.stt_order_hint'))} <a href="/support/pipeline#pipeline-stt-order">${esc(trKey('console.js.panels.providers.stt.order_more'))}</a></p></div>`;
  }

  function renderSttFormHtml(cfg) {
    const stt = ((cfg.providers || {}).stt) || {};
    const model = ((cfg.effective || {}).stt || {}).model || 'gemini-3.5-transcribe';
    const fixed = `<p class="pref-desc" id="prov-stt-fixed">Google AI Studio · <code class="pref-mono">${esc(model)}</code> — ${esc(trKey('console.js.panels.providers.stt.fixed'))}</p>`;
    return uiCard({
      tag: 'form', id: 'prov-stt-form', icon: 'mic', title: trKey('console.js.panels.providers.view.feature.stt'), split: false, attrs: { 'data-prov-form': 'stt' },
      bodyHtml: fixed
        + renderSttOrderHtml(cfg)
        + uiToggleRow({ id: 'prov-stt-enabled', label: trKey('console.js.panels.providers.stt.enabled'), desc: trKey('console.js.panels.providers.stt.enabled_hint'), checked: stt.enabled !== false })
        + uiField({ id: 'prov-stt-lang', label: trKey('console.js.panels.providers.stt.language'), controlHtml: `<input id="prov-stt-lang" name="prov-stt-lang" class="pref-input" value="${esc(stt.language || 'ko')}" maxlength="16" required/>`, hint: trKey('console.js.panels.providers.stt.language_hint') })
        + actionsWithStatus(submitBtn(trKey('console.js.panels.providers.stt.save'), 'save', 'accent'))
    });
  }

  function renderAdvancedFormHtml(cfg) {
    const gem = ((cfg.providers || {}).gemini) || {};
    const eff = cfg.effective || {};
    let fields = '<div class="console-form-grid">'
      + uiField({ id: 'prov-gemini-interval', label: trKey('console.js.panels.providers.adv.interval'), controlHtml: `<input id="prov-gemini-interval" name="prov-gemini-interval" type="number" step="0.5" min="0" class="pref-input" value="${esc(gem.min_interval != null ? gem.min_interval : 4)}" required/>` })
      + uiField({ id: 'prov-gemini-retries', label: trKey('console.js.panels.providers.adv.retries'), controlHtml: `<input id="prov-gemini-retries" name="prov-gemini-retries" type="number" step="1" min="0" class="pref-input" value="${esc(gem.max_retries != null ? gem.max_retries : 5)}" required/>` })
      + '</div>'
      + `<p class="hint">${esc(trKey('console.js.panels.providers.adv.interval_hint'))}</p>`
      + `<p class="hint" data-prov-jev-moved>${esc(trKey('console.js.panels.providers.adv.jev_moved'))} <a href="/laboratory/shadows#lab-jev">${esc(trKey('console.js.panels.providers.adv.jev_link'))}</a></p>`;
    if (!eff.is_production) {
      // The test provider exists only on a development server: the server refuses it in production.
      fields += uiToggleRow({ id: 'prov-mock-enabled', label: trKey('console.js.panels.providers.adv.mock'), desc: trKey('console.js.panels.providers.adv.mock_hint'), checked: (cfg.active_provider || '') === 'mock' });
    }
    // The tab "advanced" keeps this form out of sight until the person opens the tab (owner selection Q1: tabs).
    return uiCard({
      tag: 'form', id: 'prov-advanced-form', icon: 'settings', title: trKey('console.js.panels.providers.adv.title'), split: false, attrs: { 'data-prov-form': 'advanced' },
      bodyHtml: fields + actionsWithStatus(submitBtn(trKey('console.js.panels.providers.adv.save'), 'save', 'accent'))
    });
  }

  // The tabs of /admin/providers (owner selection Q1 of PROVIDER_SETTINGS_SIMPLIFICATION_DESIGN.md: tabs only). Each form keeps its own save and its result next to its buttons.
  const PROVIDER_TABS = [
    { id: 'status', icon: 'check-circle' },
    { id: 'model', icon: 'key' },
    { id: 'stt', icon: 'mic' },
    { id: 'advanced', icon: 'settings' }
  ];

  // The tab that holds the element of the address fragment (for example /admin/providers#prov-stt-form), selected and scrolled to after the render.
  function openProviderTabOfHash(target) {
    const id = decodeURIComponent((window.location.hash || '').slice(1));
    const el = id ? document.getElementById(id) : null;
    if (!el || !target.contains(el)) return;
    const panel = el.closest('[role="tabpanel"][data-tabpanel]');
    const tab = panel ? target.querySelector(`#prov-tabs [role="tab"][data-tab="${panel.getAttribute('data-tabpanel')}"]`) : null;
    if (tab && tab.getAttribute('aria-selected') !== 'true') tab.click();
    el.scrollIntoView({ block: 'start' });
  }

  function renderProviderManagerDOM() {
    const target = _provState.container;
    if (!target || !_provState.config) return;
    const cfg = _provState.config;
    // The card of the embedding models is outside this container in the page markup; it moves into the tab of the key and the models.
    const embedCard = document.getElementById('admin-providers-embed-card');
    if (embedCard) embedCard.remove();
    const html = {
      status: renderEffectiveCardHtml(cfg),
      model: renderKeyFormHtml(cfg) + renderModelFormHtml(cfg),
      stt: renderSttFormHtml(cfg),
      advanced: renderAdvancedFormHtml(cfg)
    };
    target.innerHTML = window.ClaireUi.renderTabs({
      id: 'prov-tabs',
      ariaLabel: trKey('console.js.panels.providers.tabs_label'),
      active: _provState.activeTab || 'status',
      tabs: PROVIDER_TABS.map(tab => ({ id: tab.id, icon: tab.icon, label: trKey(`console.js.panels.providers.tab.${tab.id}`), html: html[tab.id] }))
    });
    // The embedding models of the shared knowledge bases (console_admin.js fills that card) belong to the tab of the key and the models.
    const modelPanel = target.querySelector('[data-tabpanel="model"] .claire-panel-inner');
    if (embedCard && modelPanel) modelPanel.appendChild(embedCard);
    if (target.dataset.provBound !== '1') {
      target.dataset.provBound = '1';
      target.addEventListener('submit', onProviderFormSubmit);
      target.addEventListener('claire:tabchange', ev => { _provState.activeTab = ev.detail.tab; });
    }
    window.ClaireUi.enhance(target);
    enhanceConsoleDom();
    openProviderTabOfHash(target);
  }

  // After a save: the status card and the state of the key show the new config; the forms keep what the person typed.
  function refreshProviderStatus() {
    const cfg = _provState.config;
    const old = document.getElementById('prov-effective-card');
    if (old) old.outerHTML = renderEffectiveCardHtml(cfg);
    const sttOrder = document.getElementById('prov-stt-order');
    if (sttOrder) sttOrder.outerHTML = renderSttOrderHtml(cfg);
    const keyForm = document.getElementById('prov-key-form');
    if (keyForm) {
      const status = keyForm.querySelector('[data-prov-status]');
      const kept = status ? { text: status.textContent, state: status.getAttribute('data-state') } : null;
      keyForm.outerHTML = renderKeyFormHtml(cfg);
      const fresh = document.getElementById('prov-key-form');
      if (fresh && kept) setFormStatus(fresh, kept.state, kept.text);
    }
    enhanceConsoleDom();
  }

  function providerPayload(kind, form, action) {
    const cfg = _provState.config || {};
    const val = id => { const el = form.querySelector('#' + id); return el ? el.value : ''; };
    const checked = id => { const el = form.querySelector('#' + id); return Boolean(el && el.checked); };
    if (kind === 'key') {
      if (action === 'delete') return { providers: { gemini: { api_key: '', clear_api_key: true } } };
      const key = val('prov-gemini-key').trim();
      if (!key) return null;
      const payload = { providers: { gemini: { api_key: key } } };
      // A production server has one real provider: the save of the key makes it the main provider.
      if ((cfg.effective || {}).is_production) payload.active_provider = 'gemini';
      return payload;
    }
    if (kind === 'model') {
      const routing = {};
      for (const slot of PROVIDER_SLOTS) {
        const m = val(`prov-route-${slot}-model`);
        routing[slot] = { provider: 'inherit', model: m, effort: m ? splitGeminiModelEffort(m).effort : '' };
      }
      return { providers: { gemini: { model: val('prov-gemini-model'), embed_model: val('prov-gemini-embed-model') } }, feature_routing: routing };
    }
    if (kind === 'stt') return { providers: { stt: { enabled: checked('prov-stt-enabled'), language: val('prov-stt-lang').trim() } } };
    if (kind === 'advanced') {
      const payload = {
        providers: {
          gemini: { min_interval: parseFloat(val('prov-gemini-interval')), max_retries: parseInt(val('prov-gemini-retries'), 10) }
        }
      };
      if (form.querySelector('#prov-mock-enabled')) payload.active_provider = checked('prov-mock-enabled') ? 'mock' : 'gemini';
      return payload;
    }
    return null;
  }

  function providerErrorText(status, body) {
    const code = body && (body.error || body.detail);
    if (code === 'mock_not_allowed_in_production') return trKey('console.js.panels.providers.error.mock_production');
    return trKey('console.js.panels.save_failed') + ' ' + (code || ('HTTP ' + status));
  }

  async function checkProviderKey(form) {
    const payload = { provider: 'gemini', config: {} };
    const key = (form.querySelector('#prov-gemini-key') || {}).value || '';
    if (key.trim()) payload.config.api_key = key.trim();
    const r = await fetch(resolveRootUrl(_provState.opts, 'providers/test'), { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    const d = await r.json().catch(() => ({}));
    if (r.ok && d.ok) {
      if (d.details && Array.isArray(d.details.models) && d.details.models.length && _provState.config.providers && _provState.config.providers.gemini) {
        _provState.config.providers.gemini.available_models = d.details.models;
      }
      setFormStatus(form, 'ok', `${d.message || trKey('console.js.panels.providers.key.check_ok')} · ${nowStamp()}`);
    } else {
      setFormStatus(form, 'error', d.error || d.detail || trKey('console.js.panels.providers.key.check_failed'));
    }
  }

  async function onProviderFormSubmit(event) {
    const form = event.target && event.target.closest ? event.target.closest('form[data-prov-form]') : null;
    if (!form) return;
    event.preventDefault();
    const kind = form.getAttribute('data-prov-form');
    const action = (event.submitter && event.submitter.value) || 'save';
    // Validation of the browser on save (forms guide: route the focus to the first invalid field); the check and the delete do not need it.
    if (action === 'save' && typeof form.reportValidity === 'function' && !form.reportValidity()) return;
    const buttons = Array.from(form.querySelectorAll('button[type=submit]'));
    const done = () => buttons.forEach(b => { b.disabled = false; });
    buttons.forEach(b => { b.disabled = true; });
    try {
      if (kind === 'key' && action === 'check') {
        setFormStatus(form, 'busy', trKey('console.js.panels.providers.key.checking'));
        await checkProviderKey(form);
        return;
      }
      if (kind === 'key' && action === 'delete' && !window.confirm(trKey('console.js.panels.providers.key.delete_confirm'))) return;
      const payload = providerPayload(kind, form, action);
      if (!payload) {
        setFormStatus(form, 'error', trKey('console.js.panels.providers.key.need_key'));
        const keyEl = form.querySelector('#prov-gemini-key');
        if (keyEl) keyEl.focus();
        return;
      }
      setFormStatus(form, 'busy', trKey('console.js.common.saving'));
      const r = await fetch(resolveRootUrl(_provState.opts, 'providers'), { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        setFormStatus(form, 'error', providerErrorText(r.status, body));
        return;
      }
      _provState.config = body.config || _provState.config;
      const keyEl = form.querySelector('#prov-gemini-key');
      if (keyEl) keyEl.value = '';
      setFormStatus(form, 'ok', trKey(action === 'delete' ? 'console.js.panels.providers.key.deleted' : 'console.js.panels.providers.saved_at', { time: nowStamp() }));
      refreshProviderStatus();
      if (_provState.opts && typeof _provState.opts.onSave === 'function') _provState.opts.onSave(body);
    } catch (e) {
      setFormStatus(form, 'error', trKey('console.js.panels.error_occurred') + ' ' + (e.message || e));
    } finally {
      done();
    }
  }

  async function renderProviderManager(container, opts) {
    _provState.container = resolveContainer(container);
    _provState.opts = opts || {};
    const target = _provState.container;
    if (!target) return;
    target.innerHTML = renderPanelLoadingCard('prov-loading-card', 'bolt', 'console.js.panels.providers.title', 'console.js.panels.loading_settings');
    enhanceConsoleDom();
    try {
      const r = await fetch(resolveRootUrl(_provState.opts, 'providers'));
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
    const pdfExApp = document.getElementById('limit-pdf-exclude-appendix').checked;
    const pdfExRef = document.getElementById('limit-pdf-exclude-references').checked;
    const pdfPaperEff = document.getElementById('limit-pdf-paper-effort').value;
    const pdfDefEff = document.getElementById('limit-pdf-default-effort').value;
    const pdfClassEff = document.getElementById('limit-pdf-classifier-effort').value;

    const extBudget = parseInt(document.getElementById('limit-extract-char-budget').value, 10);
    const mergedExtBudget = parseInt(document.getElementById('limit-merged-extract-budget').value, 10);
    const slicingStrat = document.getElementById('limit-slicing-strategy').value;

    const embedBudget = parseInt(document.getElementById('limit-embed-budget').value, 10);
    const expandMax = parseInt(document.getElementById('limit-expand-max').value, 10);
    const autoExpand = document.getElementById('limit-auto-expand').checked;
    const watchIntervalDays = document.getElementById('limit-watch-interval-days').value;

    const presPdfBytes = parseInt(document.getElementById('limit-presentation-pdf-bytes').value, 10);
    const videoMax = parseInt(document.getElementById('limit-video-max-extract').value, 10);
    const videoChunk = parseInt(document.getElementById('limit-video-chunk-sec').value, 10);
    const vlmMax = parseInt(document.getElementById('limit-vlm-max-images').value, 10);

    const payload = {
      limits: {
        raw_char_budget: isNaN(rawBudget) ? 20000 : rawBudget,
        pdf_max_extract_chars: isNaN(pdfMax) ? 50000 : pdfMax,
        pdf_paper_threshold_chars: isNaN(pdfPaper) ? 15000 : pdfPaper,
        pdf_exclude_appendix: pdfExApp,
        pdf_exclude_references: pdfExRef,
        pdf_paper_effort: pdfPaperEff,
        pdf_default_effort: pdfDefEff,
        pdf_classifier_effort: pdfClassEff,
        extract_char_budget: isNaN(extBudget) ? 20000 : extBudget,
        merged_extract_char_budget: isNaN(mergedExtBudget) ? 0 : mergedExtBudget,
        slicing_strategy: slicingStrat,
        embed_char_budget: isNaN(embedBudget) ? 8000 : embedBudget,
        expand_max: isNaN(expandMax) ? 5 : expandMax,
        auto_expand: autoExpand,
        watch_interval_days: watchIntervalDays,
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
        pdf_exclude_appendix: true,
        pdf_exclude_references: true,
        pdf_paper_effort: 'high',
        pdf_default_effort: '',
        pdf_classifier_effort: 'low',
        extract_char_budget: 20000,
        merged_extract_char_budget: 0,
        slicing_strategy: 'table-exemption',
        embed_char_budget: 8000,
        expand_max: 5,
        auto_expand: true,
        watch_interval_days: 1,
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
      + uiToggleRow({ id: 'limit-pdf-exclude-appendix', label: trKey("console.js.panels.limits.pdf_exclude_appendix"), checked: lims.pdf_exclude_appendix !== false })
      + uiToggleRow({ id: 'limit-pdf-exclude-references', label: trKey("console.js.panels.limits.pdf_exclude_references"), checked: lims.pdf_exclude_references !== false })
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

    const ctxBody = uiField({ id: 'limit-embed-budget', label: trKey("console.js.panels.limits.embed_budget"), controlHtml: `<input id="limit-embed-budget" type="number" min="0" class="pref-input" value="${esc(lims.embed_char_budget ?? 8000)}"/>` });
    h += uiCard({
      id: 'limit-context-card',
      icon: 'compass',
      title: trKey("console.js.panels.limits.context_title"),
      bodyHtml: ctxBody
    });

    const expandBody = uiToggleRow({ id: 'limit-auto-expand', label: trKey("console.js.panels.limits.auto_expand"), desc: trKey("console.js.panels.limits.auto_expand_hint"), checked: lims.auto_expand !== false })
      + `<p class="hint" data-limit-merge-moved>${esc(trKey("console.js.panels.limits.same_subject_merge_moved"))} <a href="/laboratory/shadows#lab-merge">${esc(trKey("console.js.panels.limits.same_subject_merge_link"))}</a></p>`
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
    const defaultMgr = optsState.defaultSorcerer || (window.__CLAIRE_CONFIG && window.__CLAIRE_CONFIG.sorcerer) || '';

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
  // EXPORTS
  // ==========================================

  const ClaireManagePanels = {
    renderProviderManager,
    renderLimitManager,
    renderThemeManager,
    togglePassVisibility,
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
    closeThemeManager
  };

  window.ClaireManagePanels = ClaireManagePanels;
  Object.assign(window, ClaireManagePanels);
})();
