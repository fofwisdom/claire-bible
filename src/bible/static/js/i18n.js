/* Claire browser i18n runtime (I18N design Section 6.3).
 * The catalog is the inline element <script id="claire-i18n-catalog" type="application/json">, thus t() works before all network requests. The catalog files are the same ones that the Python backend reads (/static/locales/{locale}.json).
 * Load this file without defer, after the inline catalog element and before the scripts that call ClaireI18n.
 */
(function (global) {
  'use strict';
  var PLACEHOLDER = /\{([a-zA-Z0-9_]+)\}/g;
  var catalog = {};
  var currentLocale = document.documentElement.getAttribute('lang') || 'en';

  var inlineEl = document.getElementById('claire-i18n-catalog');
  if (inlineEl) {
    try { catalog = JSON.parse(inlineEl.textContent || '{}'); } catch (_) { catalog = {}; }
  }

  function t(key, params) {
    var template = Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : key;
    if (!params) return template;
    return String(template).replace(PLACEHOLDER, function (match, name) {
      return Object.prototype.hasOwnProperty.call(params, name) && params[name] != null
        ? String(params[name])
        : match;
    });
  }

  // Plural form: <baseKey>.one when count is 1 and the key exists, else <baseKey>.other. Same rule as tn() in Python.
  function tn(baseKey, count, params) {
    var all = { count: count };
    Object.keys(params || {}).forEach(function (name) { all[name] = params[name]; });
    var oneKey = baseKey + '.one';
    if (count === 1 && Object.prototype.hasOwnProperty.call(catalog, oneKey)) return t(oneKey, all);
    return t(baseKey + '.other', all);
  }

  function applyDom(root) {
    var scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    scope.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
    });
    scope.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      el.setAttribute('placeholder', t(el.getAttribute('data-i18n-placeholder')));
    });
    scope.querySelectorAll('[data-i18n-aria-label]').forEach(function (el) {
      el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria-label')));
    });
  }

  // POST /api/locale: sets or clears the claire_locale cookie. For an owner session the server also stores the choice on the account. locale: 'en', 'ko', or null / '' for automatic negotiation. Resolves to the response body; rejects with an Error whose message is the server error code.
  function setLocale(locale) {
    return fetch('/api/locale', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ locale: locale || null })
    }).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) throw new Error(body.error || 'http_' + response.status);
        return body;
      });
    });
  }

  // The server renders the page text, thus a locale change needs a new GET of the page. assign() does not send a form again.
  function reload() {
    global.location.assign(global.location.pathname + global.location.search);
  }

  global.ClaireI18n = {
    t: t,
    tn: tn,
    applyDom: applyDom,
    setLocale: setLocale,
    reload: reload,
    getLocale: function () { return currentLocale; }
  };
})(window);
