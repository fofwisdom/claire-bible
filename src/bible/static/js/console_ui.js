/* Canonical Console UI HTML helpers for /settings/*, /admin/*, and /moderation/*. */
(function (root) {
  "use strict";

  var SAFE_CARD_TAGS = { section: true, div: true, form: true, article: true };
  var ATTR_NAME_RE = /^[a-zA-Z_:][a-zA-Z0-9_.:-]*$/;

  function esc(v) {
    if (v == null) return "";
    return String(v).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function icon(name) {
    if (!name) return "";
    if (root.ClaireIcons && typeof root.ClaireIcons.svg === "function") {
      var s = root.ClaireIcons.svg(name);
      if (s) return s;
    }
    return '<span data-icon="' + esc(name) + '"></span>';
  }

  function joinClasses() {
    var out = [];
    for (var i = 0; i < arguments.length; i += 1) {
      var part = arguments[i];
      if (part && String(part).trim()) out.push(String(part).trim());
    }
    return out.join(" ");
  }

  function attrsHtml(attrs) {
    if (!attrs || typeof attrs !== "object") return "";
    var parts = [];
    Object.keys(attrs).forEach(function (key) {
      if (!ATTR_NAME_RE.test(key)) return;
      var val = attrs[key];
      if (val === false || val == null) return;
      if (val === true) parts.push(" " + key);
      else parts.push(" " + key + '="' + esc(val) + '"');
    });
    return parts.join("");
  }

  function card(opts) {
    var o = opts || {};
    var tag = SAFE_CARD_TAGS[o.tag] ? o.tag : "section";
    var cls = joinClasses("console-subcard", o.split === false ? "no-split" : "", o.extraClass);
    var idAttr = o.id ? ' id="' + esc(o.id) + '"' : "";
    var ariaAttr = o.ariaLabel ? ' aria-label="' + esc(o.ariaLabel) + '"' : "";
    var titleContent = o.titleHtml != null ? String(o.titleHtml) : esc(o.title || "");
    var iconPart = o.icon ? icon(o.icon) + " " : "";
    var badgePart = o.badgeHtml ? " " + String(o.badgeHtml) : "";
    var headingHtml = (titleContent || o.icon || o.badgeHtml) ? "<h2>" + iconPart + titleContent + badgePart + "</h2>" : "";
    var headerHtml = o.actionsHtml ? '<div class="console-card-head">' + headingHtml + String(o.actionsHtml) + "</div>" : headingHtml;
    var hintHtml = "";
    if (o.hintHtml != null && o.hintHtml !== "") hintHtml = '<p class="hint">' + String(o.hintHtml) + "</p>";
    else if (o.hint != null && o.hint !== "") hintHtml = '<p class="hint">' + esc(o.hint) + "</p>";
    var bodyHtml = o.bodyHtml != null ? String(o.bodyHtml) : "";
    return "<" + tag + ' class="' + esc(cls) + '"' + idAttr + ariaAttr + attrsHtml(o.attrs) + ">" + headerHtml + hintHtml + bodyHtml + "</" + tag + ">";
  }

  function field(opts) {
    var o = opts || {};
    var cls = joinClasses("console-field", o.extraClass);
    var forAttr = o.id ? ' for="' + esc(o.id) + '"' : "";
    var labelContent = o.labelHtml != null ? String(o.labelHtml) : esc(o.label || "");
    var controlHtml = o.controlHtml != null ? String(o.controlHtml) : "";
    var hintHtml = "";
    if (o.hintHtml != null && o.hintHtml !== "") hintHtml = '<small class="console-field-hint">' + String(o.hintHtml) + "</small>";
    else if (o.hint != null && o.hint !== "") hintHtml = '<small class="console-field-hint">' + esc(o.hint) + "</small>";
    return '<label class="' + esc(cls) + '"' + forAttr + attrsHtml(o.attrs) + ">" + labelContent + controlHtml + hintHtml + "</label>";
  }

  function toggleRow(opts) {
    var o = opts || {};
    var idAttr = o.id ? ' id="' + esc(o.id) + '"' : "";
    var labelContent = o.labelHtml != null ? String(o.labelHtml) : esc(o.label || "");
    var labelEl = o.id ? '<label for="' + esc(o.id) + '"><strong>' + labelContent + "</strong></label>" : "<strong>" + labelContent + "</strong>";
    var descHtml = "";
    if (o.descHtml != null && o.descHtml !== "") descHtml = "<span>" + String(o.descHtml) + "</span>";
    else if (o.desc != null && o.desc !== "") descHtml = "<span>" + esc(o.desc) + "</span>";
    var checkedAttr = o.checked ? " checked" : "";
    var disabledAttr = o.disabled ? " disabled" : "";
    return '<div class="pref-row"><div class="pref-row-label">' + labelEl + descHtml + '</div><input' + idAttr + ' type="checkbox" switch' + checkedAttr + disabledAttr + attrsHtml(o.attrs) + "/></div>";
  }

  function notice(html, tone, extraClass, attrs) {
    var cls = joinClasses("pref-notice", extraClass);
    var toneVal = tone || "info";
    return '<div class="' + esc(cls) + '" data-tone="' + esc(toneVal) + '"' + attrsHtml(attrs) + ">" + (html != null ? String(html) : "") + "</div>";
  }

  function pill(text, tone, extraClass, attrs) {
    var cls = joinClasses("pref-pill", extraClass);
    var toneVal = tone || "neutral";
    return '<span class="' + esc(cls) + '" data-tone="' + esc(toneVal) + '"' + attrsHtml(attrs) + ">" + esc(text) + "</span>";
  }

  function table(opts) {
    var o = opts || {};
    var headers = Array.isArray(o.headers) ? o.headers : [];
    var tblCls = joinClasses("pref-table", o.extraClass);
    var ariaAttr = o.ariaLabel ? ' aria-label="' + esc(o.ariaLabel) + '"' : "";
    var caption = o.captionHtml ? "<caption>" + String(o.captionHtml) + "</caption>" : "";
    var thead = "";
    if (headers.length) {
      thead = "<thead><tr>" + headers.map(function (h) {
        if (h && typeof h === "object") {
          var thCls = h.className ? ' class="' + esc(h.className) + '"' : "";
          var thContent = h.html != null ? String(h.html) : esc(h.label || "");
          return "<th" + thCls + attrsHtml(h.attrs) + ">" + thContent + "</th>";
        }
        return "<th>" + esc(h) + "</th>";
      }).join("") + "</tr></thead>";
    }
    var tbodyIdAttr = o.tbodyId ? ' id="' + esc(o.tbodyId) + '"' : "";
    var bodyHtml = o.bodyHtml != null ? String(o.bodyHtml) : "";
    return '<div class="pref-table-wrap"><table class="' + esc(tblCls) + '"' + ariaAttr + ">" + caption + thead + "<tbody" + tbodyIdAttr + ">" + bodyHtml + "</tbody></table></div>";
  }

  function statRow(items, attrs) {
    var list = Array.isArray(items) ? items : [];
    var inner = list.map(function (item) {
      if (typeof item === "string") return item;
      var it = item || {};
      if (it.html != null) return String(it.html);
      var valHtml = it.valueHtml != null ? String(it.valueHtml) : esc(it.value != null ? it.value : "");
      var lblHtml = it.labelHtml != null ? String(it.labelHtml) : esc(it.label != null ? it.label : "");
      return '<div class="pref-stat"' + attrsHtml(it.attrs) + "><b>" + valHtml + "</b><span>" + lblHtml + "</span></div>";
    }).join("");
    return '<div class="pref-stat-row"' + attrsHtml(attrs) + ">" + inner + "</div>";
  }

  function kv(rows, attrs) {
    var list = Array.isArray(rows) ? rows : [];
    var inner = list.map(function (row) {
      if (Array.isArray(row)) {
        return "<dt>" + esc(row[0]) + "</dt><dd>" + esc(row[1]) + "</dd>";
      }
      var r = row || {};
      var dtHtml = r.labelHtml != null ? String(r.labelHtml) : esc(r.label != null ? r.label : "");
      var ddHtml = r.valueHtml != null ? String(r.valueHtml) : esc(r.value != null ? r.value : "");
      return "<dt" + attrsHtml(r.dtAttrs) + ">" + dtHtml + "</dt><dd" + attrsHtml(r.attrs) + ">" + ddHtml + "</dd>";
    }).join("");
    return '<dl class="console-kv"' + attrsHtml(attrs) + ">" + inner + "</dl>";
  }

  function result(id, attrs) {
    var idAttr = id ? ' id="' + esc(id) + '"' : "";
    return '<p class="result" role="status" aria-live="polite"' + idAttr + attrsHtml(attrs) + "></p>";
  }

  function btn(label, opts) {
    var o = opts || {};
    var variant = o.variant ? String(o.variant) : "";
    var tokens = variant ? variant.split(/\s+/) : [];
    var isAccent = tokens.indexOf("accent") >= 0 || tokens.indexOf("pri") >= 0 || tokens.indexOf("primary") >= 0;
    var isDanger = tokens.indexOf("danger") >= 0;
    var isSm = Boolean(o.sm) || o.size === "sm" || tokens.indexOf("sm") >= 0;
    var cls = joinClasses("pref-btn", isAccent ? "pref-btn-accent" : "", isDanger ? "pref-btn-danger" : "", isSm ? "pref-btn-sm" : "", o.extraClass);
    var idAttr = o.id ? ' id="' + esc(o.id) + '"' : "";
    var iconPart = o.icon ? icon(o.icon) + " " : "";
    var labelContent = o.labelHtml != null ? String(o.labelHtml) : esc(label || "");
    if (o.href != null) {
      return '<a class="' + esc(cls) + '" href="' + esc(o.href) + '"' + idAttr + attrsHtml(o.attrs) + ">" + iconPart + labelContent + "</a>";
    }
    var btnType = o.type ? String(o.type) : "button";
    var disabledAttr = o.disabled ? " disabled" : "";
    return '<button type="' + esc(btnType) + '" class="' + esc(cls) + '"' + idAttr + disabledAttr + attrsHtml(o.attrs) + ">" + iconPart + labelContent + "</button>";
  }

  function actions(html, extraClass) {
    var cls = joinClasses("console-actions", extraClass);
    return '<div class="' + esc(cls) + '">' + (html != null ? String(html) : "") + "</div>";
  }

  root.ClaireConsoleUI = {
    esc: esc,
    icon: icon,
    card: card,
    field: field,
    toggleRow: toggleRow,
    notice: notice,
    pill: pill,
    table: table,
    statRow: statRow,
    kv: kv,
    result: result,
    btn: btn,
    actions: actions
  };
})(window);
